import logging
import os
import tarfile
from io import BytesIO

from django.conf import settings
from django.http.response import FileResponse
from django.http.response import HttpResponse, JsonResponse, Http404, HttpResponseForbidden
from django.shortcuts import render

from access.config import DIR, config

# FIXME remove
from util.files import clean_submission_dir, read_and_remove_submission_meta, \
                       read_submission_meta, write_submission_meta
from util.http import post_data
from util.monitored_dict import MonitoredDict
from util.templates import template_to_str


logger = logging.getLogger('grader.asyncjob')


# FIXME remove
def _get_course_exercise_lang(course_key, exercise_key, lang_code):
    from django.utils import translation
    (course, exercise) = config.exercise_entry(course_key, exercise_key, lang=lang_code)
    if course is None or exercise is None:
        raise Http404()
    lang_code = exercise['lang']
    translation.activate(lang_code)
    return (course, exercise, lang_code)


def _container_download_auth(request):
    logger.info("Start of %s", request.path_info)
    token = request.META.get('HTTP_AUTHORIZATION', '').split(None, 1)
    token = token[1] if len(token) == 2 and token[0].lower() == 'bearer' else None

    if token is None and settings.DEBUG:
        token = request.GET.get("token", None)

    if not token:
        raise HttpResponseForbidden("No token")

    meta = read_submission_meta(token)
    if meta is None:
        raise HttpResponseForbidden("Invalid sid")

    return meta


def _container_download_sendtar(request, path, name):
    data = BytesIO()
    with tarfile.open(mode = "w:gz", fileobj=data) as tar:
        tar.add(path, arcname='.', recursive=True)

    data.seek(0)
    response = FileResponse(data, content_type='application/gzip')
    # TODO: Django 2.0: (as_attachment=True, filename='exercise.tar.gz') does the same
    response['Content-Disposition'] = 'attachment; filename="%s"' % (name,)
    logger.info("End of %s", request.path_info)
    return response


def container_download_exercise(request):
    """
    Download exercise data from grader to container
    """
    meta = _container_download_auth(request)

    course_key = meta['course_key']
    exercise_key = meta['exercise_key']
    lang = meta['lang']

    course, exercise, lang = _get_course_exercise_lang(course_key, exercise_key, lang)
    container = exercise.get("container", {})
    if not container or "mount" not in container:
        raise Http404("Invalid exercise container info")

    exercise_path = os.path.join(DIR, course_key, container['mount'])
    return _container_download_sendtar(request, exercise_path, 'exercise.tar.gz')


def container_download_personalized(request):
    """
    Download personalized exercise data from grader to container
    """
    meta = _container_download_auth(request)
    personalized_dir = meta.get('personalized_exercise')
    if not personalized_dir:
        raise Http404("No personalization for the exercise")
    return _container_download_sendtar(request, personalized_dir, 'personalized.tar.gz')


def container_download_submission(request):
    """
    Download submission data from grader to container
    """
    meta = _container_download_auth(request)
    submission_dir = meta['dir']
    return _container_download_sendtar(request, submission_dir, 'submission.tar.gz')


def container_post(request):
    '''
    Proxies the grading result from inside container to A+
    '''
    logger.info("Container results..")
    sid = request.POST.get("sid", None)
    if not sid:
        return HttpResponseForbidden("Missing sid")

    #meta = read_and_remove_submission_meta(sid)
    meta = read_submission_meta(sid)
    if meta is None:
        return HttpResponseForbidden("Invalid sid")
    #clean_submission_dir(meta["dir"])


    data = {
        "points": int(request.POST.get("points", 0)),
        "max_points": int(request.POST.get("max_points", 1)),
    }
    for key in ["error", "grading_data"]:
        if key in request.POST:
            data[key] = request.POST[key]
    if "error" in data and data["error"].lower() in ("no", "false"):
        del data["error"]

    feedback = request.POST.get("feedback", "")
    # Fetch the corresponding exercise entry from the config.
    lang = meta["lang"]
    (course, exercise) = config.exercise_entry(meta["course_key"], meta["exercise_key"], lang=lang)
    if "feedback_template" in exercise:
        # replace the feedback with a rendered feedback template if the exercise is configured to do so
        # it is not feasible to support all of the old feedback template variables that runactions.py
        # used to have since the grading actions are not configured in the exercise YAML file anymore
        required_fields = { 'points', 'max_points', 'error', 'out' }
        result = MonitoredDict({
            "points": data["points"],
            "max_points": data["max_points"],
            "out": feedback,
            "error": data.get("error", False),
            "title": exercise.get("title", ""),
        })
        translation.activate(lang)
        feedback = template_to_str(course, exercise, None, exercise["feedback_template"], result=result)
        if result.accessed.isdisjoint(required_fields):
            alert = template_to_str(
                course, exercise, None,
                "access/feedback_template_did_not_use_result_alert.html")
            feedback = alert + feedback
        # Make unicode results ascii.
        feedback = feedback.encode("ascii", "xmlcharrefreplace")

    data["feedback"] = feedback

    if not post_data(meta["url"], data):
        write_submission_meta(sid, meta)
        return HttpResponse("Failed to deliver results", status=502)
    return HttpResponse("Ok")
