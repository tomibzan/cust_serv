from django.contrib import admin
from .models import Payment, PaymentProof, PaymentApproval

admin.site.register(Payment)
admin.site.register(PaymentProof)
admin.site.register(PaymentApproval)