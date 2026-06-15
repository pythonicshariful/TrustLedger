"""
Backup Script: Director, Customer, and Transaction Data
Run this before making any schema changes:
    python backup_data.py
"""
import os
import sys
import shutil
from datetime import datetime

# Ensure the app path is on the Python path
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app

def backup_critical_data():
    app = create_app()
    with app.app_context():
        import pandas as pd
        from models import Director, Customer, Transaction
        
        data_folder = app.config.get('DATA_FOLDER', app.instance_path)
        backup_dir = os.path.join(data_folder, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f'pre_update_backup_{timestamp}.xlsx')
        
        # --- Directors ---
        all_directors = Director.query.all()
        director_list = []
        for d in all_directors:
            total_share_val = d.total_share * d.per_share_value
            total_payable = total_share_val + d.land_value_extra_share
            due = total_payable - d.total_paid
            director_list.append({
                'ID': d.id,
                'Name': d.name,
                'Phone': d.phone,
                'Bank Name': d.bank_name,
                'Total Share': d.total_share,
                'Per Share Value': d.per_share_value,
                'Fair Cost': d.fair_cost,
                'Land Value Extra Share': d.land_value_extra_share,
                'Total Paid': d.total_paid,
                'Total Payable': total_payable,
                'Due': due,
                'Payment History': d.payment_history or ''
            })
        df_directors = pd.DataFrame(director_list)
        
        # --- Customers ---
        all_customers = Customer.query.all()
        customer_list = []
        for c in all_customers:
            customer_list.append({
                'ID': c.id,
                'Customer ID': c.customer_id,
                'Name': c.name,
                'Phone': c.phone,
                'Director': c.director.name if c.director else '',
                'Director ID': c.director_id,
                'Plot No': c.plot_no,
                'Father Name': c.father_name,
                'Mother Name': c.mother_name,
                'DOB': c.dob,
                'Religion': c.religion,
                'Profession': c.profession,
                'NID No': c.nid_no,
                'Present Address': c.present_address,
                'Permanent Address': c.permanent_address,
                'Total Price': c.total_price,
                'Down Payment': c.down_payment,
                'Monthly Installment': c.monthly_installment,
                'Total Paid': c.total_paid,
                'Due Amount': c.due_amount,
            })
        df_customers = pd.DataFrame(customer_list)
        
        # --- Transactions ---
        all_transactions = Transaction.query.all()
        tx_list = []
        for tx in all_transactions:
            tx_list.append({
                'ID': tx.id,
                'Customer ID': tx.customer.customer_id if tx.customer else '',
                'Customer Name': tx.customer.name if tx.customer else '',
                'Date': tx.date,
                'Amount': tx.amount,
                'Installment Type': tx.installment_type,
                'Bank Name': tx.bank_name,
                'Transaction ID': tx.transaction_id,
                'Remarks': tx.remarks,
                'Images': tx.images,
            })
        df_transactions = pd.DataFrame(tx_list)
        
        # Write to Excel
        with pd.ExcelWriter(backup_path, engine='openpyxl') as writer:
            df_directors.to_excel(writer, sheet_name='Directors', index=False)
            df_customers.to_excel(writer, sheet_name='Customers', index=False)
            df_transactions.to_excel(writer, sheet_name='Transactions', index=False)
            
            # Auto-fit columns
            for sheet_name in ['Directors', 'Customers', 'Transactions']:
                if sheet_name in writer.sheets:
                    ws = writer.sheets[sheet_name]
                    for col in ws.columns:
                        max_len = max((len(str(cell.value or '')) for cell in col), default=0)
                        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)
        
        print(f"\n[OK] Backup created successfully!")
        print(f"   Path: {backup_path}")
        print(f"   Directors: {len(director_list)}")
        print(f"   Customers: {len(customer_list)}")
        print(f"   Transactions: {len(tx_list)}")
        return backup_path

if __name__ == '__main__':
    backup_critical_data()
