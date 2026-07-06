from datetime import timedelta
import uuid
from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from kiems.audit_service import AuditService, attach_user_to_instance
from kiems.models import (
    CustomUser, Constituency, DailyKIEMSEntry, EntryAuditLog,
    SyncLog, KIEMSKit, Phase, Ward
)
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
from django.contrib.admin.models import ADDITION, CHANGE, DELETION


# ============================================================================
# AUTHENTICATION VIEWS
# ============================================================================

def login_user(request):
    """User login with role-based routing"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        try:
            user = CustomUser.objects.get(email=email)
            authenticated_user = authenticate(request, username=user.username, password=password)

            if authenticated_user is not None:
                login(request, authenticated_user)

                # Log the login action
                AuditService.log_entry_change(
                    entry=None,  # Not related to an entry
                    field_name='user_login',
                    old_value='',
                    new_value=f'{user.get_full_name} ({user.get_role_display()})',
                    user=authenticated_user,
                    action_type='login'
                ) if hasattr(AuditService, 'log_entry_change') else None

                # Route based on role
                role_routes = {
                    'SuperAdmin': 'super_admin_dashboard',
                    'RO': 'RO_dashboard',
                    'DRO': 'DRO_dashboard',
                    'VRA': 'VRA_dashboard',
                    'Clerk': 'clerk_dashboard',
                }

                redirect_url = role_routes.get(user.role, 'dashboard')
                return redirect(redirect_url)
            else:
                messages.error(request, 'Username or password is incorrect')
                return redirect('login_user')

        except CustomUser.DoesNotExist:
            messages.error(request, 'Email not found')
            return redirect('login_user')

    return render(request, 'login.html')


# ============================================================================
# DASHBOARD VIEWS (Read-Only)
# ============================================================================

def dashboard(request):
    """General dashboard"""
    return render(request, 'dashboard.html')


def clerk_dashboard(request):
    """Clerk dashboard - shows their entries and submissions"""
    return render(request, 'clerk_dashboard.html')


def VRA_dashboard(request):
    """Voter Registration Assistant dashboard"""
    return render(request, 'VRA.html')


def RO_dashboard(request):
    """Registration Officer dashboard"""
    return render(request, 'RO_dashboard.html')


def clerk_report(request):
    """Clerk report view"""
    return render(request, 'clerk_report.html')


def clerk_account(request):
    """Clerk account settings"""
    return render(request, 'clerk_account.html')


# ============================================================================
# SUPERADMIN DASHBOARD WITH ANALYTICS
# ============================================================================

@login_required(login_url='login_user')
@permission_required('auth.view_user', raise_exception=True)
def superadmin_dashboard(request):
    """
    SuperAdmin analytics dashboard with real database metrics
    All queries are optimized with select_related and prefetch_related
    """
    active_phase = Phase.objects.filter(active=True).first()

    if not active_phase:
        context = {
            'error': 'No active phase found. Please create one in admin.',
            'active_phase': None,
        }
        return render(request, 'superadmin_home.html', context)

    # === METRIC CALCULATIONS ===

    # 1. Total Registered Across Active Phase
    total_registered = DailyKIEMSEntry.objects.filter(
        phase=active_phase
    ).aggregate(
        total=Sum('new_registered')
    )['total'] or 0

    # 2. KIEMS Kit Status Overview
    total_kits = KIEMSKit.objects.count()
    active_kits = KIEMSKit.objects.filter(status='active').count()
    inactive_kits = KIEMSKit.objects.filter(status='inactive').count()
    repair_kits = KIEMSKit.objects.filter(status='in_repair').count()

    # 3. Sync Status
    unsynced_entries = DailyKIEMSEntry.objects.filter(
        phase=active_phase,
        is_synced=False
    ).count()
    synced_entries = DailyKIEMSEntry.objects.filter(
        phase=active_phase,
        is_synced=True
    ).count()

    # 4. Online Clerks (last activity in last 5 minutes)
    five_mins_ago = timezone.now() - timedelta(minutes=5)
    online_clerks = CustomUser.objects.filter(
        role='Clerk',
        last_login__gte=five_mins_ago
    ).count()
    total_clerks = CustomUser.objects.filter(role='Clerk').count()

    # === RECENT SUBMISSIONS ===
    recent_entries = DailyKIEMSEntry.objects.filter(
        phase=active_phase
    ).select_related(
        'kiems_kit__ward',
        'kiems_kit__constituency',
        'kiems_kit__assigned_clerk',
        'created_by'
    ).order_by('-entry_date', '-created_at')[:10]

    # === SYNC ENGINE STATUS ===
    failed_syncs = SyncLog.objects.filter(
        status='failed',
        entry__phase=active_phase
    ).select_related(
        'entry__kiems_kit'
    ).order_by('-attempted_at')[:5]

    # === AUDIT LOG ACTIVITY ===
    recent_audits = EntryAuditLog.objects.filter(
        entry__phase=active_phase
    ).select_related(
        'entry__kiems_kit',
        'edited_by'
    ).order_by('-changed_at')[:5]

    # === DAILY ACTIVITY METRICS ===
    today = timezone.now().date()
    today_entries = DailyKIEMSEntry.objects.filter(
        phase=active_phase,
        entry_date=today
    ).aggregate(
        new=Sum('new_registered'),
        transferred=Sum('transferred'),
        updated=Sum('updated')
    )

    # === WARDS & CONSTITUENCY COVERAGE ===
    constituencies = Constituency.objects.annotate(
        kit_count=Count('kits'),
        active_kit_count=Count('kits', filter=Q(kits__status='active')),
        entries_count=Count('kits__daily_entries', filter=Q(kits__daily_entries__phase=active_phase))
    ).order_by('-active_kit_count')[:5]

    # === COMPILATION DICT ===
    context = {
        'metrics': {
            'total_registered': total_registered,
            'active_kits': active_kits,
            'total_kits': total_kits,
            'inactive_kits': inactive_kits,
            'repair_kits': repair_kits,
            'unsynced_count': unsynced_entries,
            'synced_count': synced_entries,
            'online_clerks': online_clerks,
            'total_clerks': total_clerks,
            'sync_health_percentage': int((synced_entries / (synced_entries + unsynced_entries) * 100)) if (
                                                                                                                   synced_entries + unsynced_entries) > 0 else 0,
        },
        'recent_entries': recent_entries,
        'failed_syncs': failed_syncs,
        'recent_audits': recent_audits,
        'today_activity': today_entries,
        'top_constituencies': constituencies,
        'active_phase': active_phase,
    }

    return render(request, 'superadmin_home.html', context)


# ============================================================================
# SUPERADMIN MANAGEMENT VIEWS
# ============================================================================

@login_required(login_url='login_user')
@permission_required('auth.view_user', raise_exception=True)
def superadmin_user(request):
    """Manage users, assign hierarchical contexts, and toggle operational statuses"""

    if request.method == 'POST':
        action_type = request.POST.get('action_type')

        # =========================================================================
        # CREATE USER WORKFLOW
        # =========================================================================
        if action_type == 'create_user':
            username = request.POST.get('username')
            email = request.POST.get('email')
            password = request.POST.get('password')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            role = request.POST.get('role')
            is_active = request.POST.get('is_active') == 'on'

            if CustomUser.objects.filter(username=username).exists():
                messages.error(request, f"Username '{username}' is already taken.")
                return redirect('superadmin_user')

            # Create base user
            user = CustomUser.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                role=role,
                is_active=is_active
            )

            # Assign conditional boundaries using clean relational ID assignments
            if role in ['RO', 'DRO']:
                user.constituency_id = request.POST.get('constituency') or None
                user.ward_id = request.POST.get('ward') or None
            elif role == 'VRA':
                user.ward_id = request.POST.get('ward') or None
            elif role == 'Clerk':
                user.ward_id = request.POST.get('ward') or None
                user.kiems_kit_id = request.POST.get('kiems_kit') or None

            user.save()
            messages.success(request, f"User '{username}' created successfully.")
            return redirect('superadmin_user')

        # =========================================================================
        # UPDATE USER WORKFLOW
        # =========================================================================
        elif action_type == 'update_user':
            user_id = request.POST.get('user_id')
            user = get_object_or_404(CustomUser, id=user_id)

            user.first_name = request.POST.get('first_name')
            user.last_name = request.POST.get('last_name')
            user.email = request.POST.get('email')
            user.role = request.POST.get('role')

            # Handle status adjustment check (Block self-deactivation)
            target_status = request.POST.get('is_active') == 'on'
            if user == request.user and not target_status:
                messages.error(request, "Security protection: You cannot deactivate your own account.")
            else:
                user.is_active = target_status

            # Handle password updating optionally if provided
            password = request.POST.get('password')
            if password and password.strip():
                user.set_password(password)

            # Re-assign structural relations or flush old data depending on updated role
            if user.role in ['RO', 'DRO']:
                user.constituency_id = request.POST.get('constituency') or None
                user.ward_id = request.POST.get('ward') or None
                user.kiems_kit = None
            elif user.role == 'VRA':
                user.ward_id = request.POST.get('ward') or None
                user.constituency = None
                user.kiems_kit = None
            elif user.role == 'Clerk':
                user.ward_id = request.POST.get('ward') or None
                user.kiems_kit_id = request.POST.get('kiems_kit') or None
                user.constituency = None
            else:
                # If SuperAdmin, wipe physical bounds assignments
                user.constituency = None
                user.ward = None
                user.kiems_kit = None

            user.save()
            messages.success(request, f"User parameters for '{user.username}' successfully modified.")
            return redirect('superadmin_user')

    # =========================================================================
    # GET / STATUS TOGGLE / FETCH AJAX ACTIONS
    # =========================================================================
    toggle_id = request.GET.get('toggle_status_id')
    if toggle_id:
        target_user = get_object_or_404(CustomUser, id=toggle_id)

        # Rule enforcement: One cannot deactivate himself
        if target_user == request.user:
            messages.error(request, "Operational protection rule: You are not permitted to deactivate yourself.")
        else:
            target_user.is_active = not target_user.is_active
            target_user.save()
            status_str = "activated" if target_user.is_active else "deactivated"
            messages.success(request, f"Account user '{target_user.username}' has been successfully {status_str}.")
        return redirect('superadmin_user')

    # AJAX fetch endpoint for populating Edit Modal parameters safely
    if request.GET.get('action') == 'get_user_data':
        user_id = request.GET.get('user_id')
        u = get_object_or_404(CustomUser, id=user_id)
        return JsonResponse({
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'first_name': u.first_name,
            'last_name': u.last_name,
            'role': u.role,
            'is_active': u.is_active,
            'constituency': u.constituency_id if u.constituency else '',
            'ward': u.ward_id if u.ward else '',
            'kiems_kit': '',
        })

    # Render base layout payload
    users = CustomUser.objects.all().order_by('username')
    constituencies = Constituency.objects.all()
    wards = Ward.objects.all()
    kiems_kits = KIEMSKit.objects.filter(status='active')

    context = {
        'users': users,
        'constituencies': constituencies,
        'wards': wards,
        'kiems_kits': kiems_kits,
    }
    return render(request, 'superadmin_user.html', context)

@login_required(login_url='login_user')
@permission_required('auth.view_user', raise_exception=True)
def superadmin_geographic_logistics(request):
    """
    Unified View handling CRUD actions for deployment phases,
    constituencies, and county wards.
    """

    # ----------------------------------------------------
    # 1. HANDLE POST REQUESTS (Create & Update Operations)
    # ----------------------------------------------------
    if request.method == 'POST':
        action_type = request.POST.get('action_type')
        entity_id = request.POST.get('entity_id')

        # --- Phase Actions ---
        if action_type == 'create_phase':
            name = request.POST.get('name')
            start_date = request.POST.get('start_date')
            end_date = request.POST.get('end_date')
            status = request.POST.get('status')

            if name and start_date and end_date:
                Phase.objects.create(
                    id=uuid.uuid4(),
                    name=name,
                    start_date=start_date,
                    end_date=end_date,
                    active=(status == 'Active')
                )
                messages.success(request, f"Phase '{name}' created successfully.")

        elif action_type == 'update_phase':
            phase = get_object_or_404(Phase, id=entity_id)
            phase.name = request.POST.get('name')
            phase.start_date = request.POST.get('start_date')
            phase.end_date = request.POST.get('end_date')
            status = request.POST.get('status')
            phase.active = (status == 'Active')
            phase.save()
            messages.success(request, f"Phase '{phase.name}' updated successfully.")

        # --- Constituency Actions ---
        elif action_type == 'create_constituency':
            name = request.POST.get('constituency_name')
            code = request.POST.get('constituency_code')

            if name and code:
                # Using custom ID input since template supplies an explicit code value
                Constituency.objects.create(
                    id=int(code),
                    name=name
                )
                messages.success(request, f"Constituency '{name}' created successfully.")

        elif action_type == 'update_constituency':
            const = get_object_or_404(Constituency, id=entity_id)
            const.name = request.POST.get('name')
            new_code = request.POST.get('code')

            # Save name change
            const.save()

            # If primary key ID updates, handle replacement safely
            if new_code and int(new_code) != const.id:
                old_id = const.id
                const.id = int(new_code)
                const.save()
                Constituency.objects.filter(id=old_id).delete()

            messages.success(request, "Constituency updated successfully.")

        # --- Ward Actions ---
        elif action_type == 'create_ward':
            name = request.POST.get('name')
            # Fallback helper: code element might represent parent constituency link mapping
            # Alternatively defaults to the first available constituency if missing in dynamic fields.
            constituency_id = request.POST.get('constituency_id') or request.POST.get('code')

            if not constituency_id:
                # Safe fallback configuration if markup did not expose selecting custom parents
                first_const = Constituency.objects.first()
                constituency_id = first_const.id if first_const else None

            if name and constituency_id:
                constituency = get_object_or_404(Constituency, id=constituency_id)
                Ward.objects.create(
                    name=name,
                    constituency=constituency
                )
                messages.success(request, f"Ward '{name}' added successfully.")

        elif action_type == 'update_ward':
            ward = get_object_or_404(Ward, id=entity_id)
            ward.name = request.POST.get('name')
            ward.save()
            messages.success(request, f"Ward '{ward.name}' updated successfully.")

        return redirect('super_admin_geographic_logistics')

    # ----------------------------------------------------
    # 2. HANDLE GET REQUESTS (Delete Actions & Rendering)
    # ----------------------------------------------------
    delete_type = request.GET.get('delete_type')
    target_id = request.GET.get('id')

    if delete_type and target_id:
        if delete_type == 'phase':
            phase = get_object_or_404(Phase, id=target_id)
            phase.delete()
            messages.warning(request, "Operational Phase deleted.")
        elif delete_type == 'constituency':
            const = get_object_or_404(Constituency, id=target_id)
            const.delete()
            messages.warning(request, "Constituency deleted.")
        elif delete_type == 'ward':
            ward = get_object_or_404(Ward, id=target_id)
            ward.delete()
            messages.warning(request, "Ward deleted.")

        return redirect('super_admin_geographic_logistics')

    # Query structures matching template layout dependencies
    phases = Phase.objects.all().order_by('start_date')
    constituencies = Constituency.objects.all().order_by('name')
    wards = Ward.objects.all().select_related('constituency').order_by('name')

    context = {
        'phases': phases,
        'constituencies': constituencies,
        'wards': wards,
    }

    return render(request, 'superadmin_geographic_logistics.html', context)


@login_required(login_url='login_user')
@permission_required('auth.view_user', raise_exception=True)
def superadmin_kiems(request):
    """Manage KIEMS kits - create, update, delete, allocate"""

    if request.method == 'POST':
        action_type = request.POST.get('action_type')

        if action_type == 'create_kit':
            # Create new KIEMS kit
            kit_code = request.POST.get('kit_code')
            serial_number = request.POST.get('serial_number')
            date_deployed = request.POST.get('date_deployed')
            status = request.POST.get('status', 'active')

            if kit_code and serial_number and date_deployed:
                try:
                    kit = KIEMSKit.objects.create(
                        kit_code=kit_code,
                        serial_number=serial_number,
                        date_deployed=date_deployed,
                        status=status
                    )

                    # Attach user for audit logging
                    attach_user_to_instance(kit, request.user)
                    kit.save()

                    messages.success(request, f'KIEMS kit {kit.kit_code} created successfully')
                except Exception as e:
                    messages.error(request, f'Error creating kit: {str(e)}')
            else:
                messages.error(request, 'Please fill in all required fields')

        elif action_type == 'allocate_kit':
            # Allocate kit to ward and/or assign clerk
            kit_id = request.POST.get('kit_id')
            constituency_id = request.POST.get('constituency_id')
            ward_id = request.POST.get('ward_id')
            assigned_clerk_id = request.POST.get('assigned_clerk_id')

            if kit_id:
                try:
                    kit = get_object_or_404(KIEMSKit, id=kit_id)

                    if constituency_id and ward_id:
                        constituency = get_object_or_404(Constituency, id=constituency_id)
                        ward = get_object_or_404(Ward, id=ward_id)
                        kit.constituency = constituency
                        kit.ward = ward

                    if assigned_clerk_id:
                        kit.assigned_clerk_id = assigned_clerk_id
                    elif assigned_clerk_id == '':
                        kit.assigned_clerk = None

                    # Attach user for audit logging
                    attach_user_to_instance(kit, request.user)
                    kit.save()

                    messages.success(request, f'Kit {kit.kit_code} allocated successfully')
                except Exception as e:
                    messages.error(request, f'Error allocating kit: {str(e)}')
            else:
                messages.error(request, 'Kit ID is required')

        elif action_type == 'edit_kit':
            # Edit existing KIEMS kit
            kit_id = request.POST.get('kit_id')
            kit_code = request.POST.get('kit_code')
            serial_number = request.POST.get('serial_number')
            date_deployed = request.POST.get('date_deployed')
            status = request.POST.get('status')

            if kit_id:
                try:
                    kit = get_object_or_404(KIEMSKit, id=kit_id)

                    if kit_code:
                        kit.kit_code = kit_code
                    if serial_number:
                        kit.serial_number = serial_number
                    if date_deployed:
                        kit.date_deployed = date_deployed
                    if status:
                        kit.status = status

                    # Attach user for audit logging
                    attach_user_to_instance(kit, request.user)
                    kit.save()

                    messages.success(request, f'Kit {kit.kit_code} updated successfully')
                except Exception as e:
                    messages.error(request, f'Error updating kit: {str(e)}')
            else:
                messages.error(request, 'Kit ID is required')

        elif action_type == 'delete_kit':
            # Delete KIEMS kit
            kit_id = request.POST.get('kit_id')
            if kit_id:
                try:
                    kit = get_object_or_404(KIEMSKit, id=kit_id)
                    kit_code = kit.kit_code

                    # Attach user for audit logging
                    attach_user_to_instance(kit, request.user)
                    kit.delete()

                    messages.success(request, f'Kit {kit_code} deleted successfully')
                except Exception as e:
                    messages.error(request, f'Error deleting kit: {str(e)}')
            else:
                messages.error(request, 'Kit ID is required')

    # GET request - show kit management interface
    kits = KIEMSKit.objects.all().select_related(
        'constituency', 'ward', 'assigned_clerk'
    ).order_by('kit_code')

    constituencies = Constituency.objects.all().order_by('name')
    wards = Ward.objects.all().select_related('constituency').order_by('name')
    clerks = CustomUser.objects.filter(role='Clerk').order_by('username')

    # Calculate statistics
    total_kits = kits.count()
    allocated_count = kits.filter(ward__isnull=False).count()
    unallocated_count = kits.filter(ward__isnull=True).count()
    active_count = kits.filter(status='active').count()

    context = {
        'kits': kits,
        'constituencies': constituencies,
        'wards': wards,
        'clerks': clerks,
        'total_kits': total_kits,
        'allocated_count': allocated_count,
        'unallocated_count': unallocated_count,
        'active_count': active_count,
    }

    return render(request, 'superadmin_Kiems.html', context)

@login_required(login_url='login_user')
def sync_engine():
    return None

@login_required(login_url='login_user')
def security_logs():
    return None

@login_required(login_url='login_user')
def submission(request):
    # --- 1. HANDLE POST ACTIONS (Create Entry) ---
    if request.method == 'POST':
        action_type = request.POST.get('action_type')
        print(action_type)

        if action_type == 'create_entry':
            kiems_kit_id = request.POST.get('kiems_kit_id')
            entry_date = request.POST.get('entry_date')
            phase_id = request.POST.get('phase_id')
            new_registered = request.POST.get('new_registered', 0)
            transferred = request.POST.get('transferred', 0)
            updated = request.POST.get('updated', 0)
            notes = request.POST.get('notes', '')

            try:
                kiems_kit = KIEMSKit.objects.get(id=kiems_kit_id)
                phase = Phase.objects.get(id=phase_id) if phase_id else None

                DailyKIEMSEntry.objects.create(
                    kiems_kit=kiems_kit,
                    entry_date=entry_date,
                    phase=phase,
                    new_registered=int(new_registered or 0),
                    transferred=int(transferred or 0),
                    updated=int(updated or 0),
                    notes=notes,
                    is_synced=False
                )
                messages.success(request, "Daily submission entry created successfully!")
            except Exception as e:
                messages.error(request, f"Error saving entry: {str(e)}")

            return redirect('super_admin_submission')

    # --- 2. HANDLE GET ACTIONS (Delete Entry) ---
    delete_type = request.GET.get('delete_type')
    if delete_type == 'entry':
        entry_id = request.GET.get('id')
        if entry_id:
            entry = get_object_or_404(DailyKIEMSEntry, id=entry_id)
            entry.delete()
            messages.success(request, "Entry deleted successfully.")
            return redirect('super_admin_submission')

    # --- 3. FETCH AND AGGREGATE CORE DATA ---
    entries = DailyKIEMSEntry.objects.all().select_related('kiems_kit__ward', 'phase').order_by('-entry_date')
    kits = KIEMSKit.objects.all().select_related('ward')
    phases = Phase.objects.all()

    # --- 4. CALCULATE STATS ---
    synced_count = entries.filter(is_synced=True).count()
    # Check if a specific sync_error field or absence of sync establishes a "Pending" vs "Failed" state
    pending_count = entries.filter(is_synced=False).count()

    # Calculate cumulative registration count safely using DB aggregates
    total_registered = entries.aggregate(Sum('new_registered'))['new_registered__sum'] or 0

    context = {
        'entries': entries,
        'kits': kits,
        'phases': phases,
        'synced_count': synced_count,
        'pending_count': pending_count,
        'total_registered': total_registered,
    }
    return render(request, 'daily_submissions.html', context)


def ro_dashboard():
    return None


def ro_staff():
    return None


def ro_create_staff():
    return None


def ro_kits():
    return None


def ro_assign_clerk():
    return None


def ro_daily_report():
    return None


def ro_ward_report():
    return None


def ro_analytics():
    return None


def ro_submissions():
    return None


def ro_audit():
    return None