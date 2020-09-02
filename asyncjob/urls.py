from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^container-post$',
        views.container_post,
        name='container-post'),
    url(r'^container/exercise.tar.gz$',
        views.container_download_exercise,
        name='container-download-exercise'),
    url(r'^container/submission.tar.gz$',
        views.container_download_submission,
        name='container-download-submission'),
    url(r'^container/personalized.tar.gz$',
        views.container_download_personalized,
        name='container-download-personalized'),
]
