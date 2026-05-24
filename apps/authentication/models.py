import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

class Permission(models.Model):
    codename = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_custom = models.BooleanField(default=True)
    permissions = models.ManyToManyField(Permission, related_name='roles')

    def __str__(self):
        return self.name

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('category', 'agency')
        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    CATEGORY_CHOICES = (
        ('agency', 'Agency User'),
        ('external', 'External Client'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='agency')
    role_obj = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    daily_working_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8.0)
    
    # Multi-Factor Authentication
    mfa_secret = models.CharField(max_length=32, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    
    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    @property
    def has_custom_permissions(self):
        # Placeholder for complex permission checking logic
        return self.role_obj.permissions.all() if self.role_obj else []


class MFAChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mfa_challenges')
    challenge_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code_hash = models.CharField(max_length=255)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def create_for_user(cls, user, code, ttl_minutes=5):
        return cls.objects.create(
            user=user,
            code_hash=make_password(str(code)),
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self):
        return self.consumed_at is not None

    def verify(self, code):
        if self.is_consumed or self.is_expired or self.attempts >= self.max_attempts:
            return False
        self.attempts += 1
        self.save(update_fields=['attempts'])
        if check_password(str(code), self.code_hash):
            self.consumed_at = timezone.now()
            self.save(update_fields=['consumed_at'])
            return True
        return False


class PasswordResetToken(models.Model):
    """One-time password reset tokens sent via email."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_tokens')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def create_for_user(cls, user, ttl_minutes=60):
        # Invalidate any previous unused tokens for this user
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        return cls.objects.create(
            user=user,
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

    def consume(self):
        self.used_at = timezone.now()
        self.save(update_fields=['used_at'])

