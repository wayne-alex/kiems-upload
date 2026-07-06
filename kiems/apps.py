from django.apps import AppConfig


class KiemsConfig(AppConfig):
    name = 'kiems'

    def ready(self):
        import kiems.audit_service
