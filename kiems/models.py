from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Constituency(models.Model):
    """Constituency model"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name_plural = 'Constituencies'

    def __str__(self):
        return self.name


class Ward(models.Model):
    """Ward model"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    constituency = models.ForeignKey(Constituency, on_delete=models.CASCADE, related_name='wards')

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    """Custom user model with role field"""
    ROLE_CHOICES = [
        ('SuperAdmin', 'SuperAdmin'),
        ('RO', 'RO'),
        ('DRO', 'DRO'),
        ('VRA', 'VRA'),
        ('Clerk', 'Clerk'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='Clerk')
    constituency = models.ForeignKey(Constituency, on_delete=models.CASCADE, blank=True, null=True,
                                     related_name='constituencies')
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, blank=True, null=True, related_name='wards')

    # Fix the clash by adding related_name
    groups = models.ManyToManyField(
        'auth.Group',
        related_name='customuser_set',
        blank=True,
        help_text='The groups this user belongs to.',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='customuser_set',
        blank=True,
        help_text='Specific permissions for this user.',
    )

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Phase(models.Model):
    """Phase model:  This is The nam of the program e.g. Continuous Voter Registration (CVR)"""
    id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)


class KIEMSKit(models.Model):
    """KIEMS Kit model - physical voting device"""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('in_repair', 'In Repair'),
    ]

    kit_code = models.CharField(max_length=50, unique=True)
    serial_number = models.CharField(max_length=100, unique=True)
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='kits', null=True)
    constituency = models.ForeignKey(Constituency, on_delete=models.CASCADE, null=True, related_name='kits')

    # Assigned clerk
    assigned_clerk = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_kits',
        limit_choices_to={'role': 'Clerk'},
        help_text="Clerk responsible for this kit"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )

    date_deployed = models.DateField()
    date_decommissioned = models.DateField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='kits_created'
    )

    class Meta:
        ordering = ['kit_code']
        indexes = [
            models.Index(fields=['status', 'constituency']),
            models.Index(fields=['assigned_clerk']),
        ]

    def __str__(self):
        return f"{self.kit_code} ({self.ward.name})"


class DailyKIEMSEntry(models.Model):
    """Daily KIEMS entry - daily counts of registered, transferred, updated users"""

    kiems_kit = models.ForeignKey(
        KIEMSKit,
        on_delete=models.CASCADE,
        related_name='daily_entries'
    )
    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, null=True)

    entry_date = models.DateField(help_text="The date these counts apply to")

    # Core counters
    new_registered = models.IntegerField(default=0, help_text="New users registered")
    transferred = models.IntegerField(default=0, help_text="Users transferred in/out")
    updated = models.IntegerField(default=0, help_text="Users updated/modified")

    # Who created/edited
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='entries_created'
    )

    edited_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='entries_edited',
        help_text="Last user to edit this entry"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Additional context
    notes = models.TextField(blank=True)

    # Sync status
    is_synced = models.BooleanField(default=False)
    last_sync_attempt = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True)
    sync_attempts = models.IntegerField(default=0)

    class Meta:
        unique_together = ('kiems_kit', 'entry_date')
        ordering = ['-entry_date', 'kiems_kit']
        indexes = [
            models.Index(fields=['entry_date', 'is_synced']),
            models.Index(fields=['kiems_kit', 'entry_date']),
        ]

    def __str__(self):
        return f"{self.kiems_kit.kit_code} - {self.entry_date}"

    def mark_for_sync(self):
        """Mark entry as needing sync (called on edit)"""
        self.is_synced = False
        self.sync_attempts = 0
        self.sync_error = ''
        self.save(update_fields=['is_synced', 'sync_attempts', 'sync_error'])

    def mark_synced(self):
        """Mark entry as successfully synced"""
        self.is_synced = True
        self.last_sync_attempt = timezone.now()
        self.sync_error = ''
        self.sync_attempts += 1
        self.save(update_fields=['is_synced', 'last_sync_attempt', 'sync_error', 'sync_attempts'])

    def mark_sync_failed(self, error_msg):
        """Mark entry as failed to sync"""
        self.is_synced = False
        self.last_sync_attempt = timezone.now()
        self.sync_error = error_msg
        self.sync_attempts += 1
        self.save(update_fields=['is_synced', 'last_sync_attempt', 'sync_error', 'sync_attempts'])


class EntryAuditLog(models.Model):
    """Audit log for tracking changes to DailyKIEMSEntry"""

    entry = models.ForeignKey(
        DailyKIEMSEntry,
        on_delete=models.CASCADE,
        related_name='audit_logs',
        null=True,
    )

    edited_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs'
    )

    field_changed = models.CharField(
        max_length=50,
        choices=[
            ('new_registered', 'New Registered'),
            ('transferred', 'Transferred'),
            ('updated', 'Updated'),
            ('notes', 'Notes'),
        ]
    )

    old_value = models.TextField(blank=True)
    new_value = models.TextField()
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']
        indexes = [
            models.Index(fields=['entry', 'changed_at']),
        ]

    def __str__(self):
        return f"{self.entry} - {self.field_changed} by {self.edited_by.username}"


class SyncLog(models.Model):
    """Log of sync attempts to external API"""

    entry = models.ForeignKey(
        DailyKIEMSEntry,
        on_delete=models.CASCADE,
        related_name='sync_logs'
    )

    status = models.CharField(
        max_length=10,
        choices=[('success', 'Success'), ('failed', 'Failed')],
        default='failed'
    )

    error_message = models.TextField(blank=True)
    external_id = models.CharField(max_length=100, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attempted_at']
        indexes = [
            models.Index(fields=['entry', 'attempted_at']),
        ]

    def __str__(self):
        return f"{self.entry} - {self.get_status_display()} at {self.attempted_at}"
