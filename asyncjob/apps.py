from django.apps import AppConfig


class AsyncJobConfig(AppConfig):
    name = 'asyncjob'

    def ready(self):
        from grader.celery import app
        from .tasks import KubernetesEventConsumerStep
        KubernetesEventConsumerStep._add_to(app)

