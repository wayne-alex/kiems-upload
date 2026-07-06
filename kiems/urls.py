from django.contrib.auth import views as auth_views
from django.urls import path

from kiems import views

urlpatterns = [

    path('login/', views.login_user, name='login_user'),
    path('dashboard/', views.dashboard, name='dashboard'),

    path('superAdmin/dashboard/', views.superadmin_dashboard, name='super_admin_dashboard'),
    path('superadmin_user/', views.superadmin_user, name='super_admin_user'),
    path('superadmin_geographic_logistics/', views.superadmin_geographic_logistics,
         name='super_admin_geographic_logistics'),
    path('superadmin_kiems/', views.superadmin_kiems, name='super_admin_kiems'),
    path('superadmin_submission/', views.submission, name='super_admin_submission'),
    path('superadmin_security_logs/', views.security_logs, name='super_admin_security_logs'),
    path('superadmin_sync_engine/', views.sync_engine, name='super_admin_sync_engine'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login_user'), name='logout'),

    path('ro/dashboard/', views.ro_dashboard, name='ro_dashboard'),
    path('ro/staff/', views.ro_staff, name='ro_staff'),
    path('ro/staff/create/', views.ro_create_staff, name='ro_create_staff'),
    path('ro/kits/', views.ro_kits, name='ro_kits'),
    path('ro/assign-clerk/', views.ro_assign_clerk, name='ro_assign_clerk'),
    path('ro/daily-report/', views.ro_daily_report, name='ro_daily_report'),
    path('ro/ward-report/', views.ro_ward_report, name='ro_ward_report'),
    path('ro/analytics/', views.ro_analytics, name='ro_analytics'),
    path('ro/submissions/', views.ro_submissions, name='ro_submissions'),
    path('ro/audit/', views.ro_audit, name='ro_audit'),

    path('clerk/', views.clerk_dashboard, name='clerk_dashboard'),
    path('clerk_report/', views.clerk_report, name='clerk_report'),
    path('clerk_account/', views.clerk_account, name='clerk_account'),

    path('VRA/', views.VRA_dashboard, name='VRA_dashboard'),

    path('RO/', views.RO_dashboard, name='RO_dashboard'),
]
