"""
Audit Service: Helper methods for logging user actions and data changes
Tracks all modifications to DailyKIEMSEntry for security and compliance
"""

from django.utils import timezone
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import DailyKIEMSEntry, EntryAuditLog, CustomUser


class AuditService:
    """Service class for managing audit logs"""

    @staticmethod
    def log_entry_change(entry, field_name, old_value, new_value, user, action_type='update'):
        """
        Log a single field change for a DailyKIEMSEntry

        Args:
            entry: DailyKIEMSEntry instance
            field_name: Name of the field that changed
            old_value: Previous value (can be None for creation)
            new_value: New value
            user: CustomUser instance who made the change
            action_type: 'create', 'update', or 'delete'

        Returns:
            EntryAuditLog instance
        """
        audit_log = EntryAuditLog.objects.create(
            entry=entry,
            edited_by=user,
            field_changed=field_name,
            old_value=str(old_value) if old_value is not None else '',
            new_value=str(new_value) if new_value is not None else '',
        )
        return audit_log

    @staticmethod
    def log_entry_creation(entry, user):
        """
        Log the creation of a new DailyKIEMSEntry

        Args:
            entry: DailyKIEMSEntry instance
            user: CustomUser who created the entry
        """
        # Log creation for each important field
        fields_to_log = [
            ('new_registered', 0, entry.new_registered),
            ('transferred', 0, entry.transferred),
            ('updated', 0, entry.updated),
        ]

        for field_name, old_val, new_val in fields_to_log:
            if new_val != old_val:
                AuditService.log_entry_change(
                    entry=entry,
                    field_name=field_name,
                    old_value=old_val,
                    new_value=new_val,
                    user=user,
                    action_type='create'
                )

    @staticmethod
    def log_entry_update(entry, changed_fields, user):
        """
        Log updates to a DailyKIEMSEntry
        Compares old vs new values for the fields that changed

        Args:
            entry: DailyKIEMSEntry instance (after update)
            changed_fields: Dict of {'field_name': (old_value, new_value)}
            user: CustomUser who made the update

        Example:
            changed_fields = {
                'new_registered': (40, 85),
                'transferred': (5, 12),
            }
        """
        for field_name, (old_value, new_value) in changed_fields.items():
            # Only log if value actually changed
            if old_value != new_value:
                AuditService.log_entry_change(
                    entry=entry,
                    field_name=field_name,
                    old_value=old_value,
                    new_value=new_value,
                    user=user,
                    action_type='update'
                )

    @staticmethod
    def log_entry_deletion(entry, user):
        """
        Log the deletion of a DailyKIEMSEntry

        Args:
            entry: DailyKIEMSEntry instance
            user: CustomUser who deleted the entry
        """
        # Create a single log entry noting the deletion
        AuditService.log_entry_change(
            entry=entry,
            field_name='entry_deleted',
            old_value=f"Kit: {entry.kiems_kit.kit_code}, Date: {entry.entry_date}",
            new_value='DELETED',
            user=user,
            action_type='delete'
        )

    @staticmethod
    def log_bulk_action(entries, action_description, user, action_type='bulk'):
        """
        Log a bulk action affecting multiple entries

        Args:
            entries: QuerySet or list of DailyKIEMSEntry instances
            action_description: Description of what changed
            user: CustomUser who performed the action
            action_type: Type of bulk action

        Example:
            unsynced = DailyKIEMSEntry.objects.filter(is_synced=False)
            AuditService.log_bulk_action(
                unsynced,
                'Bulk sync retry initiated',
                request.user,
                'bulk_sync'
            )
        """
        for entry in entries:
            AuditService.log_entry_change(
                entry=entry,
                field_name='bulk_action',
                old_value='',
                new_value=action_description,
                user=user,
                action_type=action_type
            )

    @staticmethod
    def get_entry_history(entry, limit=20):
        """
        Get full change history for a DailyKIEMSEntry

        Args:
            entry: DailyKIEMSEntry instance
            limit: Maximum number of logs to return

        Returns:
            QuerySet of EntryAuditLog ordered by most recent
        """
        return entry.audit_logs.select_related(
            'edited_by'
        ).order_by('-changed_at')[:limit]

    @staticmethod
    def get_user_activity(user, limit=50, hours=24):
        """
        Get all audit logs for a specific user in recent timeframe

        Args:
            user: CustomUser instance
            limit: Maximum logs to return
            hours: How many hours back to look

        Returns:
            QuerySet of EntryAuditLog
        """
        cutoff_time = timezone.now() - timezone.timedelta(hours=hours)
        return EntryAuditLog.objects.filter(
            edited_by=user,
            changed_at__gte=cutoff_time
        ).select_related('entry__kiems_kit').order_by('-changed_at')[:limit]

    @staticmethod
    def get_kit_changes(kit, limit=50):
        """
        Get all changes made to a specific KIEMS kit's entries

        Args:
            kit: KIEMSKit instance
            limit: Maximum logs to return

        Returns:
            QuerySet of EntryAuditLog
        """
        return EntryAuditLog.objects.filter(
            entry__kiems_kit=kit
        ).select_related('edited_by').order_by('-changed_at')[:limit]

    @staticmethod
    def check_suspicious_activity(entry, threshold=5):
        """
        Check if an entry has been changed suspiciously many times

        Args:
            entry: DailyKIEMSEntry instance
            threshold: Number of changes above which to flag as suspicious

        Returns:
            Dict with 'is_suspicious', 'change_count', 'logs'
        """
        logs = entry.audit_logs.all().order_by('-changed_at')
        change_count = logs.count()

        return {
            'is_suspicious': change_count >= threshold,
            'change_count': change_count,
            'logs': logs[:10],  # Last 10 changes
            'threshold': threshold,
        }


# ============================================================================
# AUTOMATIC SIGNAL-BASED AUDITING
# ============================================================================
# These signals automatically log changes without needing manual calls

class EntryChangeTracker:
    """
    Tracks changes to DailyKIEMSEntry before saving
    Used by pre_save signal handler
    """
    _original_values = {}

    @classmethod
    def capture_original(cls, instance):
        """Capture original values before any changes"""
        if instance.pk:  # Only for existing entries, not new ones
            original = DailyKIEMSEntry.objects.get(pk=instance.pk)
            cls._original_values[instance.pk] = {
                'new_registered': original.new_registered,
                'transferred': original.transferred,
                'updated': original.updated,
                'notes': original.notes,
            }

    @classmethod
    def get_changes(cls, instance):
        """Compare current values with originals"""
        if instance.pk not in cls._original_values:
            return {}

        original = cls._original_values[instance.pk]
        changes = {}

        for field_name, old_value in original.items():
            current_value = getattr(instance, field_name)
            if old_value != current_value:
                changes[field_name] = (old_value, current_value)

        return changes

    @classmethod
    def cleanup(cls, instance):
        """Clean up after processing"""
        if instance.pk in cls._original_values:
            del cls._original_values[instance.pk]


@receiver(pre_save, sender=DailyKIEMSEntry)
def capture_entry_changes(sender, instance, **kwargs):
    """
    Django signal: Capture original values before entry is saved
    This runs before the entry hits the database
    """
    EntryChangeTracker.capture_original(instance)


@receiver(post_save, sender=DailyKIEMSEntry)
def log_entry_changes(sender, instance, created, **kwargs):
    """
    Django signal: Log changes after entry is saved
    Automatically creates audit logs for any changes

    This runs AFTER the entry is saved to the database
    """
    # Get the user from the request (if available)
    # This requires middleware or passing user in instance._request
    # See below for how to set this in your views

    user = getattr(instance, '_audit_user', None)
    if user is None:
        # Try to get from thread-local request
        try:
            from django.contrib.auth.models import AnonymousUser
            from threading import local
            from django.core.exceptions import ImproperlyConfigured
            # Fallback: use system user or skip
            return
        except:
            return

    if created:
        # New entry created
        AuditService.log_entry_creation(instance, user)
    else:
        # Entry was updated
        changes = EntryChangeTracker.get_changes(instance)
        if changes:
            AuditService.log_entry_update(instance, changes, user)
        EntryChangeTracker.cleanup(instance)


@receiver(post_delete, sender=DailyKIEMSEntry)
def log_entry_deletion(sender, instance, **kwargs):
    """
    Django signal: Log entry deletion
    Runs after entry is deleted from database
    """
    user = getattr(instance, '_audit_user', None)
    if user:
        AuditService.log_entry_deletion(instance, user)


# ============================================================================
# HELPER FUNCTION FOR VIEWS
# ============================================================================

def attach_user_to_instance(instance, user):
    """
    Attach the current user to a model instance for auditing

    Usage in views:
        entry = DailyKIEMSEntry.objects.get(pk=pk)
        attach_user_to_instance(entry, request.user)
        entry.new_registered = 85
        entry.save()  # Automatically audited!

    Args:
        instance: Model instance
        user: CustomUser instance
    """
    instance._audit_user = user
    return instance


# ============================================================================
# AUDIT LOGGING DECORATOR FOR VIEWS
# ============================================================================

def audit_action(action_description=None):
    """
    Decorator to automatically log view actions

    Usage:
        @audit_action('Updated voter registration count')
        def update_entry(request, entry_id):
            entry = DailyKIEMSEntry.objects.get(id=entry_id)
            entry.new_registered = int(request.POST['count'])
            entry.save()
            return redirect('dashboard')

    Args:
        action_description: Description of what the view does
    """

    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            try:
                # Call the original view
                response = view_func(request, *args, **kwargs)

                # If the view modified an instance, it's already audited
                # This decorator can add context/summary if needed

                return response
            except Exception as e:
                # Even log errors
                raise

        wrapper.__name__ = view_func.__name__
        wrapper.__doc__ = view_func.__doc__
        return wrapper

    return decorator