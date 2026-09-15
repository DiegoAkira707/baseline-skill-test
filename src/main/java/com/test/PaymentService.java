package com.test;

public class PaymentService {

    public double calculateTotal(double amount) {
        double tax = amount * 0.19;
        return amount + tax;
    }
}