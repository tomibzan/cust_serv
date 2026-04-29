from rest_framework import serializers
from .models import ServiceRequest, OrderItem


class ServiceRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRequest
        fields = '__all__'
        read_only_fields = (
            'status',
            'assigned_employee',
            'created_at',
            'resolved_at',
        )

class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['product', 'quantity']

    def create(self, validated_data):
        product = validated_data['product']

        return OrderItem.objects.create(
            **validated_data,
            price_at_time=product.price,
            product_source=product.product_source  # 🔥 AUTO ASSIGN
        )        