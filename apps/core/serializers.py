from rest_framework import serializers
from .models import Tenant, Subscription, SaaSPlan, FeatureFlag

class TenantRegistrationSerializer(serializers.ModelSerializer):
    admin_email = serializers.EmailField(write_only=True)
    admin_password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Tenant
        fields = ['id', 'name', 'subdomain', 'admin_email', 'admin_password']
        read_only_fields = ['id']

    def create(self, validated_data):
        admin_email = validated_data.pop('admin_email')
        admin_password = validated_data.pop('admin_password')
        
        # Determine database name
        db_name = f"tenant_{validated_data['subdomain']}"
        validated_data['db_name'] = db_name
        validated_data['db_user'] = "postgres"
        validated_data['db_password'] = "password" # Placeholder
        
        # 1. Create tenant metadata in management db
        tenant = Tenant.objects.create(**validated_data)
        
        # 2. Provision physical database and admin user
        from .utils import create_tenant_db, setup_tenant_db
        create_tenant_db(db_name)
        setup_tenant_db(db_name, admin_email, admin_password)
        
        return tenant


class TenantSerializer(serializers.ModelSerializer):
    current_subscription = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ['id', 'name', 'subdomain', 'db_name', 'is_active', 'created_at', 'current_subscription']

    def get_current_subscription(self, obj):
        subscription = obj.subscriptions.filter(active=True).order_by('-updated_at').first()
        if not subscription:
            return None

        return {
            'id': str(subscription.id),
            'name': subscription.name,
            'status': subscription.status,
            'seat_count': subscription.seat_count,
            'plan_name': subscription.plan.name if subscription.plan else None,
            'plan_code': subscription.plan.code if subscription.plan else None,
            'estimated_monthly_total': str(subscription.estimated_monthly_total),
            'trial_ends_at': subscription.trial_ends_at,
            'current_period_end': subscription.current_period_end,
        }


class SaaSPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaaSPlan
        fields = [
            'id', 'code', 'name', 'description', 'monthly_price', 'per_seat_price',
            'included_seats', 'trial_days', 'feature_limits', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class FeatureFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureFlag
        fields = ['id', 'key', 'name', 'description', 'enabled', 'rollout_rules', 'updated_at']
        read_only_fields = ['id', 'updated_at']


class SubscriptionSerializer(serializers.ModelSerializer):
    plan_details = SaaSPlanSerializer(source='plan', read_only=True)
    estimated_monthly_total = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'tenant', 'name', 'plan', 'plan_details', 'status', 'seat_count',
            'trial_ends_at', 'current_period_end', 'stripe_customer_id',
            'stripe_subscription_id', 'cancel_at_period_end', 'features', 'active',
            'estimated_monthly_total', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_estimated_monthly_total(self, obj):
        return str(obj.estimated_monthly_total)


