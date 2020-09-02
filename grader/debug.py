import logging

try:
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    MiddlewareMixin = object


logger = logging.getLogger('grader.debug')


class LogRequestsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        logger.warning(" -- MIDDLEWARE DEBUG -- ")
        logger.info("Request %s by %s", request.method, request.META.get('HTTP_USER_AGENT', 'unknown'))
        logger.info("Aplus event: %s", request.META.get('HTTP_X_APLUS_EVENT', 'unknown'))
