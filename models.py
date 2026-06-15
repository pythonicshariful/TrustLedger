from database import db

class Director(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    bank_name = db.Column(db.String(100))
    
    # Financials
    total_share = db.Column(db.Float, default=0.0)
    per_share_value = db.Column(db.Float, default=0.0)
    fair_cost = db.Column(db.Float, default=0.0)
    land_value_extra_share = db.Column(db.Float, default=0.0)
    
    # total_paid is now auto-computed from InstallmentPayments; kept for legacy/manual director payments
    total_paid = db.Column(db.Float, default=0.0)
    payment_history = db.Column(db.Text) # Date & Deposit text blob

    customers = db.relationship('Customer', backref='director', lazy=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.String(50), nullable=False) # User visible ID
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    plot_no = db.Column(db.String(50))
    total_price = db.Column(db.Float, default=0.0)
    down_payment = db.Column(db.Float, default=0.0)
    monthly_installment = db.Column(db.Float, default=0.0)
    total_paid = db.Column(db.Float, default=0.0)
    due_amount = db.Column(db.Float, default=0.0)

    # Number of shares this customer holds (bought from the Director)
    num_shares = db.Column(db.Float, default=1.0)
    
    director_id = db.Column(db.Integer, db.ForeignKey('director.id'), nullable=False)
    
    transactions = db.relationship('Transaction', backref='customer', lazy=True, cascade="all, delete-orphan")
    installment_payments = db.relationship('InstallmentPayment', backref='customer', lazy=True, cascade="all, delete-orphan")

    # New Fields
    father_name = db.Column(db.String(100))
    mother_name = db.Column(db.String(100))
    dob = db.Column(db.String(20)) # Date of Birth
    religion = db.Column(db.String(50))
    profession = db.Column(db.String(100))
    nid_no = db.Column(db.String(50))
    present_address = db.Column(db.String(255))
    permanent_address = db.Column(db.String(255))

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    installment_type = db.Column(db.String(100)) # Now allows installment names
    bank_name = db.Column(db.String(100))
    transaction_id = db.Column(db.String(100))
    remarks = db.Column(db.Text)
    images = db.Column(db.Text) # Comma-separated paths
    
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

class Installment(db.Model):
    """A named installment milestone, e.g., 'Piling Installment'."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)          # e.g., "Piling Installment"
    per_share_amount = db.Column(db.Float, default=0.0)       # Amount per share
    total_amount = db.Column(db.Float, default=0.0)           # Total across all shares/customers
    created_date = db.Column(db.String(20))                   # Date of creation

    payments = db.relationship('InstallmentPayment', backref='installment', lazy=True, cascade="all, delete-orphan")

class InstallmentPayment(db.Model):
    """A payment record for a specific customer in a specific installment."""
    id = db.Column(db.Integer, primary_key=True)
    installment_id = db.Column(db.Integer, db.ForeignKey('installment.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    
    # Calculated at installment creation: num_shares * per_share_amount
    total_due = db.Column(db.Float, default=0.0)
    
    # Running totals (updated on each payment entry)
    total_paid_for_installment = db.Column(db.Float, default=0.0)
    remaining_due = db.Column(db.Float, default=0.0)
    
    # Individual payment entries are stored in InstallmentPaymentEntry
    entries = db.relationship('InstallmentPaymentEntry', backref='installment_payment', lazy=True, cascade="all, delete-orphan")

class InstallmentPaymentEntry(db.Model):
    """A single partial payment entry for a customer-installment pair."""
    id = db.Column(db.Integer, primary_key=True)
    installment_payment_id = db.Column(db.Integer, db.ForeignKey('installment_payment.id'), nullable=False)
    
    date = db.Column(db.String(20), nullable=False)
    paid_amount = db.Column(db.Float, default=0.0)
    remaining_due = db.Column(db.Float, default=0.0)  # After this payment
    bank_name = db.Column(db.String(100))
    transaction_id = db.Column(db.String(100))         # Optional
    remarks = db.Column(db.Text)                       # Optional
    images = db.Column(db.Text)                        # Comma-separated filenames

class PettyCash(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'Income' or 'Expense'
    amount = db.Column(db.Float, nullable=False)
    images = db.Column(db.Text) # Comma-separated filenames

class PettyCashCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

class Bank(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    branch = db.Column(db.String(100))
    account_holder_name = db.Column(db.String(100))
    joint_name = db.Column(db.String(100))
    fhp = db.Column(db.String(100)) # Father/Husband/Parent
    address = db.Column(db.String(255))
    city = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    customer_id = db.Column(db.String(50)) # Bank's customer ID
    account_no = db.Column(db.String(50), nullable=False)
    prev_account_no = db.Column(db.String(50))
    account_type = db.Column(db.String(50)) # Savings, Current, etc.
    currency = db.Column(db.String(10))
    status = db.Column(db.String(20), default='Active') # Active/Inactive
    
    
    transactions = db.relationship('BankTransaction', backref='bank', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'bank_name': self.bank_name,
            'branch': self.branch,
            'account_holder_name': self.account_holder_name,
            'joint_name': self.joint_name,
            'fhp': self.fhp,
            'address': self.address,
            'city': self.city,
            'phone': self.phone,
            'customer_id': self.customer_id,
            'account_no': self.account_no,
            'prev_account_no': self.prev_account_no,
            'account_type': self.account_type,
            'currency': self.currency,
            'status': self.status
        }

class BankTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    cheque_no = db.Column(db.String(50))
    ref_no = db.Column(db.String(50))
    narration = db.Column(db.String(255))
    transaction_details = db.Column(db.String(255))
    debit = db.Column(db.Float, default=0.0)
    credit = db.Column(db.Float, default=0.0)
    balance = db.Column(db.Float, default=0.0) # Running balance at time of tx
    
    bank_id = db.Column(db.Integer, db.ForeignKey('bank.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date,
            'cheque_no': self.cheque_no,
            'ref_no': self.ref_no,
            'narration': self.narration,
            'transaction_details': self.transaction_details,
            'debit': self.debit,
            'credit': self.credit,
            'balance': self.balance,
            'bank_id': self.bank_id
        }
