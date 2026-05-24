from rest_framework import serializers
from django.contrib.auth import get_user_model
from decimal import Decimal
from .models import Quotation, Invoice, InvoiceLineItem, RetainerAgreement, RecurringInvoiceSchedule, Expense
from apps.authentication.serializers import UserSerializer
from apps.projects.serializers import ProjectSerializer

User = get_user_model()

class QuotationSerializer(serializers.ModelSerializer):
    client_details = UserSerializer(source='client', read_only=True)
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Quotation
        fields = [
            'id', 'client', 'client_details', 'project', 'project_details',
            'title', 'total_amount', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class InvoiceLineItemSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(required=False)

    class Meta:
        model = InvoiceLineItem
        fields = ['id', 'type', 'description', 'qty', 'rate', 'amount']
        read_only_fields = ['id']

    def validate(self, attrs):
        qty = attrs.get('qty', Decimal('1.00'))
        rate = attrs.get('rate', Decimal('0.00'))
        
        # Calculate amount automatically
        attrs['amount'] = qty * rate
        return attrs

class InvoiceSerializer(serializers.ModelSerializer):
    client_details = UserSerializer(source='client', read_only=True)
    project_details = ProjectSerializer(source='project', read_only=True)
    line_items = InvoiceLineItemSerializer(many=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'client', 'client_details', 'project', 'project_details',
            'issue_date', 'due_date', 'subtotal', 'tax_percentage', 'discount_total',
            'total_amount', 'status', 'payment_terms', 'line_items', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'subtotal', 'total_amount', 'created_at', 'updated_at']

    def validate(self, attrs):
        # We will calculate subtotal and total_amount during validation or creation/update
        line_items_data = attrs.get('line_items', [])
        
        subtotal = Decimal('0.00')
        for item_data in line_items_data:
            qty = item_data.get('qty', Decimal('1.00'))
            rate = item_data.get('rate', Decimal('0.00'))
            subtotal += qty * rate
            
        discount_total = attrs.get('discount_total', Decimal('0.00'))
        tax_percentage = attrs.get('tax_percentage', Decimal('0.00'))
        
        if discount_total > subtotal:
            raise serializers.ValidationError({"discount_total": "Discount cannot exceed subtotal."})
            
        total_amount = (subtotal - discount_total) * (Decimal('1.00') + (tax_percentage / Decimal('100.00')))
        
        # Store calculated fields in attrs
        attrs['subtotal'] = subtotal
        attrs['total_amount'] = total_amount
        return attrs

    def create(self, validated_data):
        line_items_data = validated_data.pop('line_items')
        invoice = Invoice.objects.create(**validated_data)
        
        for item_data in line_items_data:
            InvoiceLineItem.objects.create(invoice=invoice, **item_data)
            
        return invoice

    def update(self, instance, validated_data):
        line_items_data = validated_data.pop('line_items', None)
        
        # Update invoice instance
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if line_items_data is not None:
            # For simplicity, recreate line items on update or match by ID if provided
            instance.line_items.all().delete()
            for item_data in line_items_data:
                InvoiceLineItem.objects.create(invoice=instance, **item_data)
                
        return instance

class RetainerAgreementSerializer(serializers.ModelSerializer):
    client_details = UserSerializer(source='client', read_only=True)

    class Meta:
        model = RetainerAgreement
        fields = [
            'id', 'client', 'client_details', 'monthly_fee', 'included_hours',
            'hourly_rate_override', 'active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class RecurringInvoiceScheduleSerializer(serializers.ModelSerializer):
    client_details = UserSerializer(source='client', read_only=True)
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = RecurringInvoiceSchedule
        fields = [
            'id', 'client', 'client_details', 'project', 'project_details',
            'title', 'amount', 'interval', 'next_run_date', 'tax_percentage',
            'active', 'auto_send', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class ExpenseSerializer(serializers.ModelSerializer):
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Expense
        fields = [
            'id', 'project', 'project_details', 'amount', 'category',
            'description', 'is_billable', 'receipt_url', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
