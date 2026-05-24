from decimal import Decimal
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import status, views, viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .serializers import (
    TenantRegistrationSerializer, TenantSerializer,
    SaaSPlanSerializer, FeatureFlagSerializer, SubscriptionSerializer,
)
from .models import Tenant, Subscription, SaaSPlan, FeatureFlag
from .audit import create_audit_log
from .permissions import IsSuperAdminUser

class TenantRegistrationView(views.APIView):
    permission_classes = [IsSuperAdminUser]

    def post(self, request):
        serializer = TenantRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            tenant = serializer.save()
            create_audit_log(
                request=request,
                action='tenant.provisioned',
                resource_type='tenant',
                resource_id=tenant.id,
                details={'tenant_name': tenant.name, 'subdomain': tenant.subdomain},
                tenant=tenant,
            )
            return Response({
                "id": tenant.id,
                "name": tenant.name,
                "subdomain": tenant.subdomain
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TenantViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer
    permission_classes = [IsSuperAdminUser]

    @action(detail=True, methods=['post'])
    def suspend(self, request, pk=None):
        tenant = self.get_object()
        tenant.is_active = False
        tenant.save(update_fields=['is_active'])
        create_audit_log(
            request=request,
            action='tenant.suspended',
            resource_type='tenant',
            resource_id=tenant.id,
            details={'subdomain': tenant.subdomain},
            tenant=tenant,
        )
        return Response({'status': 'suspended'})

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        tenant = self.get_object()
        tenant.is_active = True
        tenant.save(update_fields=['is_active'])
        create_audit_log(
            request=request,
            action='tenant.activated',
            resource_type='tenant',
            resource_id=tenant.id,
            details={'subdomain': tenant.subdomain},
            tenant=tenant,
        )
        return Response({'status': 'active'})


class SaaSPlanViewSet(viewsets.ModelViewSet):
    queryset = SaaSPlan.objects.all().order_by('monthly_price')
    serializer_class = SaaSPlanSerializer
    permission_classes = [IsSuperAdminUser]


class FeatureFlagViewSet(viewsets.ModelViewSet):
    queryset = FeatureFlag.objects.all().order_by('key')
    serializer_class = FeatureFlagSerializer
    permission_classes = [IsSuperAdminUser]

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        flag = self.get_object()
        flag.enabled = not flag.enabled
        flag.save(update_fields=['enabled', 'updated_at'])
        return Response(FeatureFlagSerializer(flag).data)


class SuperAdminDashboardView(views.APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request):
        active_subscriptions = Subscription.objects.filter(active=True).select_related('plan')
        mrr_total = Decimal('0.00')
        active_trials = 0
        for subscription in active_subscriptions:
            mrr_total += subscription.estimated_monthly_total
            if subscription.status == 'trialing':
                active_trials += 1

        total_tenants = Tenant.objects.count()
        active_tenants = Tenant.objects.filter(is_active=True).count()
        churn_rate = round(((total_tenants - active_tenants) / total_tenants) * 100, 2) if total_tenants else 0

        payload = {
            'stats': {
                'mrr': str(mrr_total),
                'arr': str(mrr_total * 12),
                'active_trials': active_trials,
                'churn_rate': churn_rate,
                'active_tenants': active_tenants,
                'total_tenants': total_tenants,
            },
            'tenant_summary': {
                'by_status': {
                    'active': active_tenants,
                    'suspended': max(total_tenants - active_tenants, 0),
                },
                'subscription_status': list(
                    active_subscriptions.values('status').annotate(count=Count('id')).order_by('status')
                ),
            },
            'system_health': {
                'db_pool': {'active': active_tenants, 'capacity': 50},
                'cpu_load_percent': min(20 + active_tenants, 95),
                'cache_hit_ratio': '98.4%',
                'slow_queries': [
                    {
                        'duration_ms': 452,
                        'query': "SELECT * FROM authentication_user WHERE category = 'external'",
                        'tenant': 'sample-workspace',
                    },
                    {
                        'duration_ms': 321,
                        'query': "SELECT SUM(monthly_price) FROM core_subscription WHERE active = TRUE",
                        'tenant': 'global_root',
                    },
                ],
            },
        }
        return Response(payload)


def get_request_tenant(request):
    subdomain = getattr(request, 'tenant_subdomain', None) or request.headers.get('X-Tenant-Subdomain')
    if not subdomain:
        return None
    return Tenant.objects.using('default').filter(subdomain=subdomain).first()


class WorkspaceBillingView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tenant = get_request_tenant(request)
        if not tenant:
            return Response({'detail': 'Tenant context is required.'}, status=status.HTTP_400_BAD_REQUEST)

        subscription = Subscription.objects.filter(tenant=tenant, active=True).select_related('plan').order_by('-updated_at').first()
        if not subscription:
            plan = SaaSPlan.objects.filter(code='starter', is_active=True).first() or SaaSPlan.objects.filter(is_active=True).first()
            trial_days = plan.trial_days if plan else 14
            subscription = Subscription.objects.create(
                tenant=tenant,
                name=plan.name if plan else 'Starter',
                plan=plan,
                status='trialing',
                seat_count=1,
                trial_ends_at=timezone.now() + timezone.timedelta(days=trial_days),
                current_period_end=timezone.now() + timezone.timedelta(days=30),
                active=True,
            )

        return Response({
            'tenant': TenantSerializer(tenant).data,
            'subscription': SubscriptionSerializer(subscription).data,
            'available_plans': SaaSPlanSerializer(SaaSPlan.objects.filter(is_active=True), many=True).data,
        })

    def post(self, request):
        tenant = get_request_tenant(request)
        if not tenant:
            return Response({'detail': 'Tenant context is required.'}, status=status.HTTP_400_BAD_REQUEST)

        action_name = request.data.get('action')
        subscription = Subscription.objects.filter(tenant=tenant, active=True).select_related('plan').order_by('-updated_at').first()
        if not subscription:
            return Response({'detail': 'No active subscription found.'}, status=status.HTTP_404_NOT_FOUND)

        if action_name == 'update_seats':
            seats = int(request.data.get('seat_count', subscription.seat_count))
            if seats < 1:
                return Response({'detail': 'seat_count must be at least 1'}, status=status.HTTP_400_BAD_REQUEST)
            subscription.seat_count = seats
            subscription.save(update_fields=['seat_count', 'updated_at'])
            return Response(SubscriptionSerializer(subscription).data)

        if action_name == 'change_plan':
            plan_id = request.data.get('plan_id')
            plan = SaaSPlan.objects.filter(id=plan_id, is_active=True).first()
            if not plan:
                return Response({'detail': 'Plan not found.'}, status=status.HTTP_404_NOT_FOUND)
            subscription.plan = plan
            subscription.name = plan.name
            subscription.status = 'active'
            subscription.current_period_end = timezone.now() + timezone.timedelta(days=30)
            subscription.save(update_fields=['plan', 'name', 'status', 'current_period_end', 'updated_at'])
            return Response(SubscriptionSerializer(subscription).data)

        if action_name == 'create_checkout_session':
            checkout_url = f"https://checkout.mock-stripe.local/{tenant.subdomain}/{subscription.id}"
            return Response({
                'checkout_url': checkout_url,
                'mode': 'mock',
                'detail': 'Stripe keys unavailable; returned mock checkout URL.',
            })

        return Response({'detail': 'Unsupported action.'}, status=status.HTTP_400_BAD_REQUEST)


