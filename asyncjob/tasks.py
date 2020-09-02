from functools import partial

from celery import bootsteps, shared_task
from celery.exceptions import Ignore
from celery.utils.log import get_task_logger
from kombu import Consumer, Exchange, Queue


logger = get_task_logger(__name__)
task = partial(shared_task, bind=True, ignore_result=True)


@task()
def container_completed(self, state, meta, times):
    print("Container completed with {}".format(state))
    print("  meta:", meta)
    print("  times:", times)



## Schedule jobs

def _acceptSubmission(request, course, exercise, post_url, sdir):
    '''
    Queues the submission for grading.
    '''
    uids = get_uid(request)
    attempt = int(request.GET.get("ordinal_number", 1))

    if "submission_url" in request.GET:
        surl = request.GET["submission_url"]
        surl_missing = False
    else:
        LOGGER.warning("submission_url missing from a request")
        surl = request.build_absolute_uri(reverse('test-result'))
        surl_missing = True

    # Order container for grading.
    c = _requireContainer(exercise)

    course_extra = {
        "key": course["key"],
        "name": course["name"],
    }
    exercise_extra = {
        "key": exercise["key"],
        "title": exercise.get("title", None),
        "resources": c.get("resources", {}), # Unofficial param, implemented differently later
        "require_constant_environment": c.get("require_constant_environment", False) # Unofficial param, implemented differently later
    }
    if exercise.get("personalized", False):
        exercise_extra["personalized_exercise"] \
            = select_generated_exercise_instance(course, exercise, uids, attempt)

    job = AsyncJob(
        course_key=course['key'],
        exercise_key=exercise['key'],
        lang=translation.get_language(), # FIXME
        upload_url=surl,
        submission_meta={
            'uids': uids,
            'personalized_exercise': exercise_extra.get("personalized_exercise"),
        },
    )

    sid = os.path.basename(sdir)
    write_submission_meta(sid, {
        "url": surl,
        "dir": sdir,
        'personalized_exercise': exercise_extra.get("personalized_exercise"),
        "course_key": course["key"],
        "exercise_key": exercise["key"],
        "lang": translation.get_language(),
    })
    r = invoke([
        settings.CONTAINER_SCRIPT,
        sid,
        request.scheme + "://" + request.get_host(),
        c["image"],
        os.path.join(DIR, course["key"], c["mount"]),
        sdir,
        c["cmd"],
        json.dumps(course_extra),
        json.dumps(exercise_extra),
    ])
    LOGGER.debug("Container order exit=%d out=%s err=%s",
        r["code"], r["out"], r["err"])
    qlen = 1

    return render_template(request, course, exercise, post_url,
        "access/async_accepted.html", {
            "error": r['code'] != 0,
            "accepted": True,
            "wait": True,
            "missing_url": surl_missing,
            "queue": qlen
        })




def invoke_script(args):
    from util.files import create_submission_dir, save_submitted_file, \
        clean_submission_dir, write_submission_file, write_submission_meta
    from util.shell import invoke
    r = invoke(args)
    logger.debug("Container order exit=%d out=%s err=%s",
        r["code"], r["out"], r["err"])


## AMQP in queue

kubernetes_events = Queue(
    'kubernetes_events',
    Exchange('kubernetes_events'),
    'pod_events')

class KubernetesEventConsumerStep(bootsteps.ConsumerStep):
    @classmethod
    def _add_to(cls, app):
        app.steps['consumer'].add(cls)

    def get_consumers(self, channel):
        return [Consumer(
            channel,
            queues=[kubernetes_events],
            callbacks=[kubernetes_event_handler],
            accept=['json'],
        )]

def kubernetes_event_handler(body, message):
    print('Pod {}: {} ({})'.format(
        body.get('meta', {}).get('pod_name'),
        body.get('state', '?'),
        message.properties.get('correlation_id', ''),
    ))
    print("  meta:", body.get('meta'))
    print("  times:", body.get('times'))
    message.ack()
