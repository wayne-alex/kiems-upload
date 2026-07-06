from django.contrib import admin

from kiems.models import CustomUser, Phase, Constituency, Ward, KIEMSKit, DailyKIEMSEntry

# Register your models here.
admin.site.register(CustomUser)
admin.site.register(Phase)
admin.site.register(Constituency)
admin.site.register(Ward)
admin.site.register(KIEMSKit)
admin.site.register(DailyKIEMSEntry)