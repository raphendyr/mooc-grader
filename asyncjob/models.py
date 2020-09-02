from enum import Enum
from datetime import datetime, timezone

from django.contrib.postgres.fields import JSONField
from django.db import models


class FieldEnum(Enum):
    @classmethod
    def choices(cls):
      return [(key.value, key.name) for key in cls]


class ContainerState(FieldEnum):
    CREATED = 'c'   # received by MOOC-Grader
    ORDERED = 'o'   # send to executor (Celery or Kubernetes)
    SCHEDULED = 's' # job is scheduled (Celery has picked the task, Kubernetes has selected host)
    RUNNING = 'r'   # job is executing (Celery is running docker, Kubernetes Pod is alive)
    COMPLETED = 'f' # job has completed with success or failure


class UploadState(FieldEnum):
    PENDING = 'p'   # upload is not tried yet, waiting to be scheduled
    SCHEDULED = 's' # upload task added to queue
    SUCCEEDED = 'o' # upload has succeeded correctly
    FAILED = 'f'    # upload has failed, reupload required


class AsyncJob(models.Model):
    # submission
    course_key = models.CharField(max_length=256)
    exercise_key = models.CharField(max_length=1024)
    lang = models.CharField(max_length=8)
    submission_meta = JSONField()

    # container
    container_ref = models.CharField(
        max_length=128,
        unique=True)
    container_state = models.CharField(
        max_length=1,
        choices=ContainerState.choices(),
        default=ContainerState.CREATED.value)

    # upload
    upload_url = models.URLField()
    upload_state = models.CharField(
        max_length=1,
        choices=UploadState.choices(),
        default=UploadState.PENDING.value)
    upload_state_updated = models.DateTimeField(
        null=True)
    upload_code = models.PositiveSmallIntegerField(
        default=0)
    upload_attempt = models.PositiveSmallIntegerField(
        default=0)
    upload_at = models.DateTimeField(
        null=True)

    def _prepare_container_state(self, new_state):
        if isinstance(new_state, ContainerState):
            new_state = new_state.value
        return new_state

    def _prepare_upload_state(self, new_state):
        if isinstance(new_state, UploadState):
            new_state = new_state.value
        if new_state != self.upload_state:
            self.upload_state_updated = datetime.now(tz=timezone.utc)
        return new_state

    def _prepare_upload_code(self, code):
        self.upload_attempt += 1
        self.upload_at = datetime.now(tz.timezone.utc)
        return code

    def __setattr__(self, name, value):
        # if `_prepare_<name>` exists, call it before setting the value
        func = getattr(self, '_prepare_' + name, None)
        if func is not None and callable(func):
            value = func(value)
        super().__setattr__(name, value)
