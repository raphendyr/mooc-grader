"""
Simple schema forms.

Functions to map old style mooc-grader config `fields` and `files` to mix format `fields`
Functions to map from mix format `fields` to json schema, angular schema form, and mooc-grader filename map
Functions to parse post data
Functions to validate post data against a schema

"""
import gettext
from collections import ChainMap, OrderedDict, deque
from typing import Dict
from urllib.parse import quote_plus, unquote_plus
from functools import partial

#from django.utils.translation import gettext_lazy as _
#from django.utils import translation import get_language
get_language = lambda: 'fi' # FIXME

format_lazy = lambda s, *a, **kw: s.format(*a, **kw) # FIXME


from django.utils.functional import lazy
def _translate_lazy(default, translations):
    lang = get_language()
    return translations.get(lang, default)
def translate_lazy(string, catalog):
    if not catalog or string not in catalog:
        raise KeyError
    return lazy(_translate_lazy, str)(string, catalog[string])


ConfigError = ValueError # FIXME

# schema types: null, boolean, object, array, number, integer, or string
# types in `fields` in mooc-grader config
BASIC_TYPES = frozenset(('file', 'number', 'integer', 'string', 'text'))
CONTAINER_TYPES = frozenset(('object', 'fieldset', 'section', 'div'))
STATIC_TYPES = frozenset(('help',))
NAMELESS_TYPES = CONTAINER_TYPES - {'object'} | STATIC_TYPES
FIELD_TYPES = BASIC_TYPES | CONTAINER_TYPES | STATIC_TYPES

FIELD_TYPE_MAP = {
    # field type -> schema type
    'text': 'string',
}
FIELD_TYPE_MAP.update({k: None for k in STATIC_TYPES})
FIELD_DATA_MAP = {
    # field key -> (schema key, required type)
    'title': ('title', str),
    'description': ('description', str),
}
LAYOUT_TYPE_MAP = {
    # field type -> layout type
    'integer': 'text',
    'number': 'text',
    'string': 'text',
    'text': 'textarea',
}
LAYOUT_DATA_MAP = {
    # field key -> (layout key, required type)
    'fieldHtmlAttrs': ('fieldHtmlAttrs', Dict[str, str]),
    'fieldHtmlClass': ('fieldHtmlClass', str),
    'htmlAttrs': ('htmlAttrs', Dict[str, str]),
    'htmlClass': ('htmlClass', str),
    'labelHtmlAttrs': ('labelHtmlAttrs', Dict[str, str]),
    'labelHtmlClass': ('labelHtmlClass', str),
    'validationMessage': ('validationMessage', str),
    # 'accept': ('fieldHtmlAttrs.accept', str),
    # 'inputmode': ('fieldHtmlAttrs.inputmode', str),
    # 'pattern': ('fieldHtmlAttrs.pattern', str),
}
TRANSLATED = frozenset((
    # both
    'title',
    'description',
    # layout only
    'helpvalue', # when type=help
    'message', # when widget=message
    'placeholder',
    'validationMessage',
))


def _quote_input(path):
    return '/'.join(quote_plus(x) for x in path)


def _quote_i18n(path):
    return '[' + ':'.join(quote_plus(str(x)) for x in path) + ']'


def _quote_human(path):
    escaped = '."[]'
    quote = lambda s: '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    escape = lambda s: quote(s) if any(c in s for c in escaped) else s
    out = ''.join(
        ('.' + escape(x)) if isinstance(x, str) else ('[' + escape(str(x)) + ']')
        for x in path
    )
    if out.startswith('.l'):
        return 'layout' + out[2:]
    elif out.startswith('.s'):
        return 'schema' + out[2:]
    else:
        return out


def _copy_entries(map_, from_, to):
    for fkey, (tkey, req_type) in map_.items():
        if fkey in from_:
            try:
                value = from_[fkey]
                if hasattr(req_type, '_name'):
                    if req_type._name == 'Dict':
                        if not isinstance(value, dict):
                            raise ValueError
                        dest = to.setdefault(tkey, {})
                        ktype, vtype = req_type.__args__
                        for key, val in value.items():
                            if not isinstance(key, ktype) or not isinstance(val, vtype):
                                raise ValueError
                            dest[key] = val
                elif isinstance(value, req_type):
                    to[tkey] = value
                elif value is False or value is None:
                    continue
                else:
                    raise ValueError
            except ValueError:
                raise ValueError("Invalid configuration: expected %s for %s" % (req_type, fkey))


def _find_from_schema(schema, path):
    if not path:
        return shcema, path
    while len(path) > 1:
        next_, *path = path
        type_ = schema.get('tyoe', 'object')
        if type_ == 'object':
            schema = schema.get('properties', {}).get(next_, {})
        elif type_ == 'array':
            schema = schema.get('items', {}).get(next_, {})
    return schema, path[0]


def _find_from_nested(data, path, default=None):
    if not path:
        return data, path
    while len(path) > 1:
        next_, *path = path
        data = data.get(next_, {})
    return data.get(path[0], default)

def _set_to_nested(data, path, value):
    if not path:
        return
    while len(path) > 1:
        next_, *path = path
        data_ = data.setdefault(next_, {})
        if not isinstance(data_, dict):
            # NOTE: loses data
            data[next_] = data = {}
        else:
            data = data_
    if path[0] in data:
        raise ValueError("Nested value exists!")
    data[path[0]] = value



### SCHEMA + LAYOUT



def _field_to_schema(name, path, field_type, field):
    """
    Create a json schema object

    returns:
        {
            type: schema type
            pattern: regexp pattern
            title:
            description:
        }
    """
    schema_type = FIELD_TYPE_MAP.get(field_type, field_type)
    if schema_type is None:
        return None

    schema_obj = {
        'type': schema_type,
    }
    _copy_entries(FIELD_DATA_MAP, field, schema_obj)

    if schema_type == 'string':
        pattern = field.get('pattern')
        if pattern:
            if not pattern.startswith(r'^'):
                pattern = r'^' + pattern
            if not pattern.endswith(r'$'):
                pattern += r'$'
            schema_obj['pattern'] = pattern
        # TODO: maxLength, minLength
    #elif schema_type in ('number', 'integer'):
        # TODO: multipleOf, maximum, exclusiveMaximum, minimum, exclusiveMinimum
    elif schema_type == 'file':
        schema_obj['filename'] = field.get('filename') or '/'.join(path)

    return schema_obj


def _field_to_layout(name, path, field_type, field):
    """
    Create a layout entry from a simple field data

    Based on a specification:
        https://github.com/json-schema-form/angular-schema-form/blob/master/docs/index.md#form-definitions

    returns:
        {
            key: "address.street"       # The dot notatin to the attribute on the model
            type: "text"                # Type of field
            title: "Street"             # Title of field, taken from schema if available
            notitle: false              # Set to true to hide title
            description: "Street name"  # A description, taken from schema if available, can be HTML
            validationMessage:          # A custom validation error message
                "Oh noes, please write a proper address"
            placeholder: "Input..."     # placeholder on inputs and textarea
            readonly: true              # Same effect as readOnly in schema. Put on a fieldset or array
                                        # and their items will inherit it.
            htmlClass: "street foobar"  # CSS Class(es) to be added to the container div
            htmlAttrs: {}               # extra HTML attributes for the container element
            fieldHtmlClass: "street"    # CSS Class(es) to be added to field input (or similar)
            fieldHtmlAttrs: {}          # extra HTML attributes for the field element
            labelHtmlClass: "street"    # CSS Class(es) to be added to the label of the field (or similar)
            labelHtmlAttrs: {}          # extra HTML attributes for the label element
            # copyValueTo: ["address.street"], # Copy values to these schema keys.
            # condition: "person.age < 18" # Show or hide field depending on an angular expression
        }
    """
    layout_type = LAYOUT_TYPE_MAP.get(field_type, field_type)
    layout_obj = {'type': layout_type}
    if name and path:
        layout_obj['key'] = path[0] if len(path) == 1 else path
    _copy_entries(LAYOUT_DATA_MAP, field, layout_obj)

    if field_type == 'integer':
        layout_obj.setdefault('fieldHtmlAttrs', {})['inputmode'] = 'numeric'
        layout_obj.setdefault('validationMessage', _("Can contain an integer number"))
    elif field_type == 'number':
        layout_obj.setdefault('fieldHtmlAttrs', {})['inputmode'] = 'decimal'
        layout_obj.setdefault('validationMessage', _("Can contain a decimal number"))
    elif field_type == 'file':
        # accept
        accept = field.get('accept', None)
        if accept is None:
            ext = field.get('filename', name).rpartition('.')[2]
            if ext:
                accept = '.' + ext
        if accept:
            layout_obj.setdefault('fieldHtmlAttrs', {})['accept'] = accept

    # title
    if not field.get('title'): # '' or False
        layout_obj['notitle'] = True

    # validationMessage
    validationMessage = field.get('validationMessage')
    if validationMessage:
        layout_obj['validationMessage'] = validationMessage

    # pattern
    if layout_type == 'text':
        pattern = field.get('pattern', None)
        if pattern is None:
            if field_type == 'number':
                pattern = r'[-+]?[0-9]*(\.[0-9]+)?([eE][-+]?[0-9]+)?'
            elif field_type == 'integer':
                pattern = r'[-+]?[0-9]*'
        if pattern:
            pattern = pattern.lstrip(r'^').rstrip(r'$')
            layout_obj.setdefault('fieldHtmlAttrs', {})['pattern'] = pattern
            layout_obj.setdefault('validationMessage',
                format_lazy(
                    _("Content must match regular expression '{pattern}'"),
                    pattern=pattern))

    return layout_obj


def _create_object(fields, *, path=None):
    schema = {'type': 'object'}
    properties = schema.setdefault('properties', {})
    required_properties = []
    layout = []
    filenames = {}
    if path is None:
        path = ()

    for i, field in enumerate(fields):
        name = field.get('name', None)
        field_type = field.get('type', None)
        required = field.get('required', field_type == 'file')

        full_path = path + (name,) if name is not None else path

        if field_type in CONTAINER_TYPES:
            if name is not None:
                schema_obj, sub_layout, sub_filenames = _create_object(
                    field.get('fields', ()),
                    path=full_path)
                properties[name] = schema_obj
                if required:
                    required_properties.append(name)
                # FIXME
                #if schema_obj.get('required'):
                #    required_properties.append(name) # build required chain
                #    schema_obj['default'] = {} # ensure sub scheme requirements are validated
            elif field_type in NAMELESS_TYPES:
                schema_obj, sub_layout, sub_filenames = _create_object(
                    field.get('fields', ()),
                    path=path)
                properties.update(schema_obj['properties'])
                required_properties.extend(schema_obj.get('required', ()))
            else:
                raise ValueError("Name is required with type '%s'" % (field_type,))

            layout_obj = _field_to_layout(name, full_path, field_type, field)
            layout_obj['items'] = sub_layout
            layout.append(layout_obj)

            for filename, input_names in sub_filenames.items():
                filenames.setdefault(filename, []).extend(input_names)

        elif field_type in BASIC_TYPES:
            if name is None:
                raise ValueError("Name is required with type '%s'" % (field_type,))

            input_name = _quote_input(full_path)
            filename = field.get('filename', None) or '/'.join(full_path)
            filenames.setdefault(filename, []).append(input_name)

            # defaults for schema and layout
            if field_type == 'file' and not field.get('filename'):
                field['filename'] = filename
            title = field.get('title')
            if title in (None, True):
                if field_type == 'file':
                    field['title'] = filename
                elif title is True and name:
                    field['title'] = name.capitalize()

            schema_obj = _field_to_schema(name, full_path, field_type, field)
            if schema_obj is not None:
                properties[name] = schema_obj
                if required:
                    required_properties.append(name)

            layout_obj = _field_to_layout(name, full_path, field_type, field)
            layout.append(layout_obj)

        elif field_type is None or field_type in STATIC_TYPES:
            layout.append(field)
        else:
            raise ValueError("Unsupported field type '%s': %s" % (field_type, field))

    if required_properties:
        schema['required'] = required_properties

    return (schema, layout, filenames)


def build_schema(fields, *, id_=None, title=None):
    schema, layout, filenames = _create_object(fields)

    # check duplicate outputs
    duplicates = {filename: paths for filename, paths in filenames.items() if len(paths) > 1}
    if duplicates:
        dups_s = ','.join("{%s} -> %s" % (', '.join(names), fn) for fn, names in duplicates.items())
        raise ValueError("Multiple fields point to the same filename: %s" % (dups_s,))

    # meta info
    schema['$schema'] = 'https://json-schema.org/draft/2019-09/schema'
    if id_ is not None:
        schema['$id'] = id_
    if title:
        schema['title'] = title

    return (schema, layout)


def _cleaned_field(field, field_registry):
    if isinstance(field, str):
        return {'type': 'help', 'helpvalue': field}
        #return {'widget': 'message', 'message': field} # TODO: newer format
    elif not isinstance(field, dict):
        raise ValueError("Field must be a string or a dict")

    ftype = field.get('type', None)
    if ftype is None:
        field['type'] = ftype = 'text' if 'rows' in field else 'string'
    if ftype not in FIELD_TYPES:
        raise ValueError("Unknown field type '%s'" % (ftype,))

    if 'rows' in field and ftype == 'text':
        field.setdefault('fieldHtmlAttrs', {})['rows'] = field['rows']

    if ftype == 'help':
        helpvalue = field.get('helpvalue') or field.get('value')
        if helpvalue is None:
            raise ValueError("Help field must contain 'helpvalue' or 'value'")
        return {'type': 'help', 'helpvalue': helpvalue}

    name = field.get('name')
    if name:
        if name in field_registry:
            raise ValueError("Duplicate field name '%s'" % (name))
        field_registry.add(name)
    elif ftype not in NAMELESS_TYPES:
        raise ValueError("Name is required for field type '%s'" % (ftype,))

    if ftype in CONTAINER_TYPES:
        if 'fields' not in field:
            raise ValueError("Container tipes required 'fields'")
        sub_ns = set()
        field['fields'] = [_cleaned_field(field, sub_ns) for field in field['fields']]

    return field


def parse_fields_and_files(course, exercise):
    fields = OrderedDict()
    seen = set()

    for i, field in enumerate(exercise.get('fields', ())):
        try:
            field = _cleaned_field(field, seen)
        except ValueError as error:
            # TODO: error path sub index etc.
            raise ConfigError("Field at index %d raised an error: %s" % (i, error))
        name = field.get('name', i)
        fields[name] = field

    for i, field in enumerate(exercise.get('files', ())):
        if isinstance(field, str):
            field = {'name': field}
        elif not isinstance(field, dict):
            raise ConfigError("Files entry must be a string or a dict. Entry at index %d." % (i,))

        try:
            filename = field['name']
        except KeyError:
            raise ConfigError("Invalid file, missing name at index %d." % (i,))
        name = field.get('field', filename)
        entry = fields.setdefault(name, {})
        entry['type'] = 'file'
        entry['name'] = name
        entry['filename'] = filename
        for k in ('title', 'required', 'accept'):
            if k in field:
                entry[k] = field[k]

    if not fields:
        raise ConfigError("No fields parsed from `fields` or `files`. Have you configured either?")

    return tuple(fields.values())


def collect_translations(schema, layout=None):
    translations = {}

    def reg(path, obj, key, string, default=False):
        path = (*path, key)
        if path not in translations or translations[path][2]:
            translations[path] = (string, obj, default)
        elif not default:
            raise RuntimeError("Duplicate non-default translation string for %s, old: %s"
                % (_quote_human(path), translations[path]))

    # TODO: to support '$ref', this could be implemented with `jsonschema.Validator`

    def recurse(base, name, obj):
        path = (*base, name) if name is not None else base
        if 'title' not in obj and isinstance(name, str):
            reg(path, obj, 'title', name.capitalize(), True)
        for key, value in obj.items():
            if key in TRANSLATED:
                if isinstance(value, str):
                    reg(path, obj, key, value)
                else:
                    # TODO: warn
                    pass
            elif key == 'properties':
                for name, item in value.items():
                    recurse(path, name, item)
            elif key == 'items':
                for i, item in enumerate(value):
                    recurse(path, i, item)

        if 'key' in obj:
            ref = obj['key']
            if isinstance(ref, str):
                ref = (ref,)
                node, schema_name = _find_from_schema(schema, ref)
                schema_obj = node.get('properties', {}).get(schema_name, {})
                if 'title' not in obj:
                    reg(path, obj, 'title', schema_name.capitalize(), True)
                for key, value in schema_obj.items():
                    if key in TRANSLATED and key not in obj:
                        reg(path, obj, key, schema_obj[key], True)

    recurse(('s',), None, schema)
    if layout:
        for i, item in enumerate(layout):
            recurse(('l',), i, item)

    return translations


def build_translation_map(translations, default=None):
    if not translations:
        return
    if default is None:
        default = next(iter(translations.keys())) # raises StopIteration, if there are no keys
    elif default not in translations:
        raise ValueError("No translations for default language '%s'" % (default,))

    others = tuple((lang for lang in translations.keys() if lang != default))
    all_ = (default, *others)

    map_ = {}
    for lang in all_:
        for path, item in translations[lang].items():
            map_.setdefault(path, {})[lang] = item

    i18n = {}
    alternatives = {}
    for path, trans in map_.items():
        try:
            def_str, def_obj, def_def = trans[default]
        except KeyError:
            raise ValueError(
                "Unable to find translation in lang %s for string in %s. "
                "This is likely due to mismatch of schema and layout structures "
                "between different translations. "
                "Sorry, unable to provide more details in this phase."
                % (default, _quote_human(path))
            )

        any_ = not def_def
        relevant = {}
        for lang in others:
            str_, _obj, def_ = trans.get(lang, (None, None, None))
            if str_ is not None and str_ != def_str:
                relevant[lang] = str_
                any_ |= not def_

        if not any_ or not relevant:
            # Nothing to translate here..
            continue

        for alt_id, alt in alternatives.get(def_str, ()):
            if relevant == alt:
                id_ = alt_id
                break
        else:
            name = _quote_i18n(path)
            # no translation yet
            if len(def_str) > len(name)*2:
                id_ = name
            else:
                id_ = def_str
                #for i in range(3):
                for i in range(1):
                    if id_ in i18n:
                        id_ += '*'
                    else:
                        break
                else:
                    if id_ in i18n:
                        id_ = name
            while id_ in i18n:
                id_ += '='

            # NOTE: make _copy_, so if default entry is added, it doesn't break matching
            alternatives.setdefault(def_str, []).append((id_, {**relevant}))
            i18n[id_] = relevant

        # string in the original object must be updated
        if def_def or id_ != def_str:
            def_obj[path[-1]] = id_
            print(" >> update %s = %s" % (_quote_human(path), id_))
            if id_ != def_str:
                relevant[default] = def_str

    return i18n



### GRADER PREPARE



from jsonschema import Draft7Validator, validators
def _validator_with_default_feature(validator_class):
    validate_properties = validator_class.VALIDATORS['properties']

    # FIXME
    def set_defaults(validator, properties, instance, schema):
        for property, subschema in properties.items():
            if 'default' in subschema:
                instance.setdefault(property, subschema['default'])
        yield from validate_properties(validator, properties, instance, schema)

    def properties_validator(validator, properties, instance, schema):
        if not validator.is_type(instance, "object"):
            return

        for property, subschema in properties.items():
            # set 'default' values
            if 'default' in subschema:
                instance.setdefault(property, subschema['default'])
            # default path
            if property in instance:
                subinst = instance[property]
                if subschema.get('type') in ('number', 'integer'):
                    try:
                        subinst = {'number': float, 'integer': int}[subschema.get('type')](subinst)
                    except ValueError:
                        pass
                    else:
                        instance[property] = subinst
                for error in validator.descend(
                    instance[property],
                    subschema,
                    path=property,
                    schema_path=property,
                ):
                    yield error
            # descent to types, to calidate required etc.
            elif subschema.get('type') == 'object':
                for error in validator.descend(
                    {},
                    subschema,
                    path=property,
                    schema_path=property,
                ):
                    yield error

    def file_checker(checker, instance):
        return isinstance(instance, str)

    def filename_validator(validator, filename, instance, schema):
        #if not validator.is_type(instance, "object"):
        #    return

        if not hasattr(validator, '_files'):
            validator._files = set()
        validator._files.add(filename)

    new = validators.extend(validator_class,
        # redefine 'properties' validator
        validators={
            #'properties': set_defaults,
            'properties': properties_validator,
            #'filename': filename_validator,
        },
        type_checker=validator_class.TYPE_CHECKER.redefine_many({
            'file': file_checker,
        }),
    )
    # set_defaults doesn't work when validating schemas themselfs
    new.check_schema = validator_class.check_schema
    return new

Validator = _validator_with_default_feature(Draft7Validator)
#Validator = Draft7Validator


def prepare_template_context(schema, layout=None, *, i18n=None):
    if layout is None:
        layout = ["*"]

    # handle "*": replace with all root keys, which are not referenced
    all_keys = set()
    asterisk_at = None
    for i, field in enumerate(layout):
        if field == '*':
            asterisk_at = i
        elif isinstance(field, str):
            all_keys.add(field)
        elif isinstance(field, dict):
            key = field.get('key')
            if key is not None:
                all_keys.add(key if isinstance(key, str) else key[0])
    if asterisk_at is not None:
        layout = (
            layout[:asterisk_at] +
            [key for key in schema.get('properties', {}) if key not in all_keys] +
            layout[asterisk_at+1:]
        )

    # main "loop"
    def recurse(layout_):
        items = []
        for field in layout_:
            # resolve key and actual field
            if isinstance(field, str):
                key = (field,)
                field = None
            elif isinstance(field, tuple) and len(field) == 2:
                key, field = field
            elif isinstance(field, dict):
                key = field.get('key', None)
            else:
                raise ValueError
            if isinstance(key, str):
                key = (key,)

            # create ctx (or skip)
            if field:
                ctx = ChainMap({}, field)
            else:
                ctx = ChainMap()

            # connect scheme and input data
            if key is not None:
                ctx['key'] = key
                ctx['name'] = name = _quote_input(key)

                # TODO: personalisation

                # scheme
                node, schema_name = _find_from_schema(schema, key)
                ctx['required'] = schema_name in node.get('required', ())
                scheme = node.get('properties', {}).get(schema_name, {})
                if scheme:
                    ctx.maps.append(scheme)

            # recurse to nested elements
            ctx_type = ctx.get('type')
            if ctx_type == 'object':
                ctx['items'] = recurse(
                    ((*key, k), f)
                    for k, f in ctx.get('properties', {}).items()
                )
            elif ctx_type in CONTAINER_TYPES:
                ctx['items'] = recurse(ctx.get('items', ()))

            # replace translated
            if i18n:
                for prop in TRANSLATED:
                    try:
                        ctx[prop] = translate_lazy(ctx[prop], i18n)
                    except KeyError:
                        # key not in ctz or no translation for it
                        if prop in ctx:
                            print(" >> no i18n for %s" % (ctx[prop],))
                        pass
                    else:
                        print(' >> lazy i18n -> %s' % (ctx[prop],))

            # strip empty map
            if not ctx.maps[0]:
                if len(ctx.maps) > 1:
                    ctx = ctx.parents
                elif field:
                    ctx = field

            items.append(ctx)
        return items
    return recurse(layout)



### PER REQUEST



def parse_data(data):
    nested = {}
    for input_name, value in sorted(data.items(), key=lambda x: (len(x[0]), *x), reverse=True):
        parts = input_name.split('/')
        # skip '' and '/...'
        if parts and parts[0]:
            path = [unquote_plus(x) for x in parts]
            try:
                _set_to_nested(nested, path, value)
            except ValueError:
                print(" >> ignored input: '%s=%s' duplicate value or nested" % (input_name, value))
        else:
            print(" >> ignored input: '%s=%s'" % (input_name, value))
    return nested


def validate(schema, data):
    validator = Validator(schema)
    errors = {}
    for error in validator.iter_errors(data):
        if error.validator == 'required':
            name = error.message.split("'", 2)[1]
            path = tuple(error.absolute_path) + (name,)
            message = _("This field is required")
        else:
            path = error.absolute_path
            message = _("Invalid content")
        name = _quote_input(path)
        errors.setdefault(name, []).append(message)
    return errors


def create_template_context(base_context, data=None, errors=None):
    if data is None and errors is None:
        return base_context
    if data is None:
        data = {}
    if errors is None:
        errors = {}

    def recurse(fields):
        context = []
        for field in fields:
            ctx = {}

            # container
            if 'items' in field:
                ctx['items'] = recurse(field['items'])

            if 'name' in field:
                name = field['name']
                try:
                    ctx['errors'] = errors[name]
                except KeyError:
                    pass
                try:
                    ctx['value'] = data[name]
                except KeyError:
                    pass

            context.append(field.new_child(ctx) if ctx else field)
        return context
    return recurse(base_context)




if __name__ == '__main__':
    print("#"*80)
    print("#"*80)

    import os
    from collections import defaultdict
    from pprint import pprint
    from json import dumps
    from yaml import dump, safe_load
    import yaml
    from django.utils.functional import Promise
    _print = lambda n, x: print("# ------ %s ------\n%s" % (n, x))
    yprint = lambda n, x: _print(n, dump(x, sort_keys=False, allow_unicode=True))
    pprint = yprint
    jprint = lambda n, x: _print(n, dumps(x, ensure_ascii=False, indent=2))

    yaml.add_representer(ChainMap, (lambda d, m: d.represent_dict(m.items())))
    yaml.add_representer(tuple, (lambda d, t: d.represent_list(t)))
    yaml.add_multi_representer(Promise, (lambda d, s: d.represent_str(str(s))))

    localedir = '/home/jaakko/Develop/apluslms/mooc-grader/locale'
    trans = defaultdict(gettext.NullTranslations)
    trans.update({
        lang: gettext.translation(
            'django',
            localedir=localedir,
            languages=[lang],
            fallback=True)
        for lang in os.listdir(localedir)
    })

    config_fi = safe_load("""
key: test_exercise

title: testi harjoitus

fields:
- <p>Start</p>
- type: section
  fields:
  - name: name
    title: Nimi
    pattern: ab.*ba
  - name: age
    type: integer
- |
  <h1>HTML lohko</h1>
  <p>Jep, tämä on monirivinen HTML lhko</p>
  <p>Lisää rivejä...</p>
- type: file
  name: alikansio/hello1.py
- name: hello2
  filename: dummy
  title: Tiedosto 2 (Python)
  htmlClass: hello2-class
  htmlAttrs:
    data-foo: bar
- type: object
  name: address
  title: Osoite
  required: true
  fields:
  - name: num
    type: integer
    title: Postinumero
  - name: street
    type: number
    required: true

files:
- field: hello2
  name: alikansio/hello2.py
  title: false
- name: alikansio/hello3.py
  title: Kolmas tiedosto

""")
    config_en = safe_load("""
key: test_exercise

title: test exercise

fields:
- <p>Start</p>
- type: section
  fields:
  - name: name
    pattern: ab.*ba
  - name: age
    type: integer
    title: Name
- |
  <h1>A block of HTML</h1>
  <p>Yes, this is a multiline HTML block</p>
  <p>Some more lines...</p>
- type: file
  name: alikansio/hello1.py
- name: hello2
  filename: dummy
  title: File 2 (Python)
  htmlClass: hello2-class
  htmlAttrs:
    data-foo: bar
- type: object
  name: address
  required: true
  fields:
  - name: num
    type: integer
    title: Name
  - name: street
    type: number
    required: true

files:
- field: hello2
  name: alikansio/hello2.py
  title: false
- alikansio/hello3.py

""")

    POST = safe_load("""
alikansio%2Fhello1.py: hello1.py
alikansio%2Fhello3.py: hello3.py
hello2: hello2.py
age: foo
address/street: "200.35"
address/num: "20"
address/num: "40"
address: dummy
/grader/lang: fi
""")

    ### Compile phase
    print("#"*20, "Compile phase")
   
    def_lang = 'en'
    course = {'key': 'test_course'}
    config = {'en': config_en, 'fi': config_fi}
    collected_translations = {}
    pprint("exercise config", config)

    langs = (def_lang, *(lang for lang in config.keys() if lang != def_lang))
    for lang in langs:
        fields_ = parse_fields_and_files(course, config[lang])
        pprint("internal fields (%s)" % (lang,), fields_)

        _ = trans[lang].gettext # TODO: should be passed to functions
        schema_, layout_ = build_schema(fields_, title=config[lang].get('title'))
        if lang == def_lang:
            schema, layout = schema_, layout_
        yprint("json schema (%s)" % (lang,), schema_)
        yprint("angular schema - layout (%s)" % (lang,), layout_)

        translations_ = collect_translations(schema_, layout_)
        collected_translations[lang] = translations_

    _ = lambda x: x

    if len(set(len(trans) for trans in collected_translations.values())) != 1:
        raise RuntimeError

    translations_d = {
        lang: {
            _quote_human(path): string + (' (def)' if default else '')
            for path, (string, obj, default) in trans.items()
        } for lang, trans in collected_translations.items()}
    yprint("translations", translations_d)

    i18n = build_translation_map(collected_translations, default=def_lang)
    yprint("form_schema", schema)
    yprint("form_layout", layout)
    jprint("form_i18n", i18n)


    ### Prepare phase
    print("#"*20, "Prepare phase")

    prepared_context = prepare_template_context(schema, layout, i18n=i18n)
    #yprint("prepared context", prepared_context)


    ### Request phase
    _ = trans[get_language()].gettext
    print("#"*20, "Request phase")

    yprint("POST data", POST)
    data = parse_data(POST)
    errors = validate(schema, data)
    yprint("parsed data", data)
    yprint("validation errors", errors)

    context = create_template_context(prepared_context, POST, errors)
    yprint("template rendering context, lang=%s" % (get_language(),), context)
