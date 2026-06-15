import pandas as pd
import openpyxl
from models import Director, Customer, PettyCash, Transaction, Bank, BankTransaction, Installment, InstallmentPayment, InstallmentPaymentEntry
import os
import shutil
from datetime import datetime
from datetime import datetime
from database import db
from telegram_utils import send_telegram_document, log_debug
from werkzeug.utils import secure_filename
from flask import current_app

EXCEL_FILENAME = 'accounting_master.xlsx'

def sync_to_excel():
    """
    Fetches all customers and directors from the database,
    creates a consolidated table, sorts by Director,
    and writes to the Master Excel file.
    """
    try:
        # ... logic to create dataframes ...
        # Since I am replacing a large chunk, I must be careful not to delete the data preparation logic.
        # However, the user instruction said "logic.py" at "sync_to_excel".
        # The previous view showed the function is long.
        # I should use multi_replace or targeted replace.
        # But I need to wrap the whole function in try...except for logging?
        # The function already has a try...except block starting at line 187.
        # I will just update the imports and the existing try...except block.
        pass
    except:
        pass

# Wait, I cannot use ReplacementContent with "..." placeholder logic.
# I need to be precise.
# Let's use 2 replacements. 
# 1. Imports
# 2. The try-except block at the end.

    directors = Director.query.all()
    # Create a mapping of Director ID to Name for easy lookup, although we can join in query
    # But let's fetch all customers joined with directors
    
    customers = Customer.query.join(Director).add_columns(
        Director.name.label('director_name'),
        Customer.customer_id,
        Customer.name,
        Customer.phone,
        Customer.plot_no,
        Customer.total_price,
        Customer.down_payment,
        Customer.monthly_installment,
        Customer.total_paid,
        Customer.due_amount
    ).all()
    
    # Prepare data for DataFrame
    data = []
    for row in customers:
        # row is a tuple: (CustomerObj, director_name, ...) if we didn't refine add_columns carefully
        # With add_columns, it returns a Result object (named tuple like)
        
        # Let's simplify: Query customers and access relationship
        pass

    # Refined Query
    all_customers = Customer.query.all()
    
    export_list = []
    for c in all_customers:
        export_list.append({
            'Director Name': c.director.name,
            'Customer ID': c.customer_id,
            'Customer Name': c.name,
            'Phone': c.phone,
            'Plot No': c.plot_no,
            'Total Price': c.total_price,
            'Down Payment': c.down_payment,
            'Monthly Installment': c.monthly_installment,
            'Total Paid': c.total_paid,
            'Due Amount': c.due_amount
        })
    
    if not export_list:
        # Create empty dataframe with columns if no data
        df = pd.DataFrame(columns=[
            'Director Name', 'Customer ID', 'Customer Name', 'Phone', 'Plot No',
            'Total Price', 'Down Payment', 'Monthly Installment', 'Total Paid', 'Due Amount'
        ])
    else:
        df = pd.DataFrame(export_list)
        # Smart Sorting: Sort by Director Name, then Customer Name
        df = df.sort_values(by=['Director Name', 'Customer Name'])

    # --- Director Summary Data ---
    all_directors = Director.query.all()
    all_installments = Installment.query.all()
    total_inst_per_share = sum((inst.per_share_amount or 0.0) for inst in all_installments)
    
    director_list = []
    
    # Image Columns:
    # 1. SL NO. | 2. Share name | 3. Total share | 4. Per share value | 5. Fair Cost 
    # 6. Total share value | 7. Land value of Extra share | 8. Total share+ Extra share Value
    # 9. Total Paid Until date | 10. Date & Deposit | 11. B.Name | 12. DUE
    
    for i, d in enumerate(all_directors, start=1):
        total_payable = (d.total_share or 0.0) * total_inst_per_share
        due = total_payable - (d.total_paid or 0.0)
        
        director_list.append({
            'SL NO.': i,
            'Share name': d.name,
            'Total share': d.total_share,
            'Installment Sum (Per Share)': total_inst_per_share,
            'Total Payable': total_payable,
            'Total Paid Until date': d.total_paid,
            'Date & Deposit': d.payment_history or '',
            'B.Name': d.bank_name or '',
            'DUE': due
        })
        
    df_directors = pd.DataFrame(director_list)
    
    # Petty Cash Sheet
    all_entries = PettyCash.query.order_by(PettyCash.date).all()
    petty_list = []
    
    running_balance = 0
    for e in all_entries:
        if e.type == 'Income':
            running_balance += e.amount
        else:
            running_balance -= e.amount
            
        petty_list.append({
            'Date': e.date,
            'Description': e.description,
            'Category': e.category,
            'Type': e.type,
            'Income': e.amount if e.type == 'Income' else 0,
            'Expense': e.amount if e.type == 'Expense' else 0,
            'Balance': running_balance,
            'Images': e.images
        })
        
    df_petty = pd.DataFrame(petty_list)
    
    # Transactions Sheet
    all_transactions = Transaction.query.order_by(Transaction.date).all()
    tx_list = []
    for tx in all_transactions:
        tx_list.append({
            'ID': tx.id,
            'Date': tx.date,
            'Customer ID': tx.customer.customer_id,
            'Customer Name': tx.customer.name,
            'Amount': tx.amount,
            'Installment Type': tx.installment_type,
            'Bank Name': tx.bank_name,
            'Transaction ID': tx.transaction_id,
            'Remarks': tx.remarks,
            'Images': tx.images
        })
    df_transactions = pd.DataFrame(tx_list)

    # Bank Sheets
    all_banks = Bank.query.all()
    bank_list = []
    for b in all_banks:
        bank_list.append({
            'Bank Name': b.bank_name,
            'Branch': b.branch,
            'Account Holder': b.account_holder_name,
            'Joint Name': b.joint_name,
            'FHP': b.fhp,
            'Address': b.address,
            'City': b.city,
            'Phone': b.phone,
            'Customer ID': b.customer_id,
            'Account No': b.account_no,
            'Prev Account No': b.prev_account_no,
            'Account Type': b.account_type,
            'Currency': b.currency,
            'Status': b.status
        })
    df_banks = pd.DataFrame(bank_list)
    
    all_bank_tx = BankTransaction.query.order_by(BankTransaction.date).all()
    bank_tx_list = []
    for tx in all_bank_tx:
        bank_tx_list.append({
            'Bank Account No': tx.bank.account_no,
            'Date': tx.date,
            'Cheque No': tx.cheque_no,
            'Ref No': tx.ref_no,
            'Narration': tx.narration,
            'Transaction Details': tx.transaction_details,
            'Debit': tx.debit,
            'Credit': tx.credit,
            'Balance': tx.balance
        })
    df_bank_tx = pd.DataFrame(bank_tx_list)

    # --- New Installment Sheets ---
    all_installments = Installment.query.all()
    inst_summary_list = []
    for inst in all_installments:
        total_collected = sum(sum((e.paid_amount or 0.0) for e in ip.entries) for ip in inst.payments)
        total_due = sum((ip.total_due or 0.0) for ip in inst.payments)
        inst_summary_list.append({
            'ID': inst.id,
            'Installment Name': inst.name,
            'Date Created': inst.created_date,
            'Per Share Amount': inst.per_share_amount,
            'Total Commitment': total_due,
            'Total Collected': total_collected,
            'Remaining Due': total_due - total_collected
        })
    df_inst_summary = pd.DataFrame(inst_summary_list)

    all_payment_entries = InstallmentPaymentEntry.query.join(InstallmentPayment).join(Installment).order_by(InstallmentPaymentEntry.date.desc()).all()
    pay_breakdown_list = []
    for entry in all_payment_entries:
        pay_breakdown_list.append({
            'Date': entry.date,
            'Installment Name': entry.installment_payment.installment.name,
            'Customer Name': entry.installment_payment.customer.name,
            'Customer ID': entry.installment_payment.customer.customer_id,
            'Paid Amount': entry.paid_amount,
            'Destination': entry.bank_name, # This stores Cash or Bank ID string
            'Transaction ID': entry.transaction_id,
            'Remarks': entry.remarks
        })
    df_pay_breakdown = pd.DataFrame(pay_breakdown_list)

    # Write to Excel
    # We use engine='openpyxl' for xlsx
    try:
        # Determine Excel Path based on DATA_FOLDER
        from flask import current_app
        data_folder = current_app.config.get('DATA_FOLDER', '.')
        
        # Use simple filename if in dev mode main, else DATA_FOLDER
        if __name__ == '__main__':
             target_excel_path = EXCEL_FILENAME 
        else:
             excel_name = f"{secure_filename(current_app.config['COMPANY_NAME']).lower()}_master.xlsx"
             target_excel_path = os.path.join(data_folder, excel_name)
             
        with pd.ExcelWriter(target_excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Master_Data', index=False)
            df_directors.to_excel(writer, sheet_name='Directors_Summary', index=False)
            df_petty.to_excel(writer, sheet_name='Petty_Cash', index=False)
            df_transactions.to_excel(writer, sheet_name='Transactions', index=False)
            df_banks.to_excel(writer, sheet_name='Banks', index=False)
            df_bank_tx.to_excel(writer, sheet_name='Bank_Transactions', index=False)
            df_inst_summary.to_excel(writer, sheet_name='Installments_Summary', index=False)
            df_pay_breakdown.to_excel(writer, sheet_name='Payment_Breakdown', index=False)
            
            # Formatter function
            workbook = writer.book
            
            all_sheets = ['Master_Data', 'Directors_Summary', 'Petty_Cash', 'Transactions', 'Banks', 'Bank_Transactions', 'Installments_Summary', 'Payment_Breakdown']
            for sheet_name in all_sheets:
                if sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    for column in worksheet.columns:
                        max_length = 0
                        column = [cell for cell in column]
                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(cell.value)
                            except:
                                pass
                        adjusted_width = (max_length + 2)
                        worksheet.column_dimensions[column[0].column_letter].width = adjusted_width
                
        print(f"Successfully synced to {target_excel_path}")
        log_debug(f"Synced Excel to: {target_excel_path}")
        
        # --- Auto-Backup Logic ---
        # Only create backup if it's the real file, not test
        if 'test_' not in EXCEL_FILENAME:
            # Create backups folder in DATA_FOLDER
            backup_dir = os.path.join(data_folder, 'backups')
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
                
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            comp_id = secure_filename(current_app.config['COMPANY_NAME']).lower()
            backup_filename = f"{comp_id}_backup_{timestamp}.xlsx"
            backup_path = os.path.join(backup_dir, backup_filename)
            
            shutil.copy2(target_excel_path, backup_path)
            print(f"Backup created: {backup_path}")
            log_debug(f"Backup created at: {backup_path}")
            
            # Send to Telegram
            log_debug("Initiating Telegram backup upload...")
            if send_telegram_document(backup_path, caption=f"Auto-Backup: {backup_filename}\nSent from {current_app.config['COMPANY_NAME']}"):
                 log_debug("Excel backup sent to Telegram.")
            else:
                 log_debug("Failed to send Excel backup to Telegram.")

            # Also send the live DB file
            # DB is in DATA_FOLDER
            db_path = os.path.join(data_folder, 'project.db')
            if os.path.exists(db_path):
                 log_debug(f"Sending DB backup: {db_path}")
                 if send_telegram_document(db_path, caption=f"Live DB Backup: {timestamp}\nSent from {current_app.config['COMPANY_NAME']}"):
                     log_debug("DB backup sent to Telegram.")
                 else:
                     log_debug("Failed to send DB backup to Telegram.")
            else:
                 log_debug(f"DB file not found at {db_path}")
            
    except Exception as e:
        print(f"Error syncing to Excel: {e}")
        log_debug(f"CRITICAL ERROR in sync_to_excel: {e}")

def restore_from_excel(file_path):
    """
    Restores the database from the given Excel file.
    WARNING: This wipes all current data.
    """
    try:
        # Read Excel Sheets
        df_master = pd.read_excel(file_path, engine='openpyxl', sheet_name='Master_Data')
        df_directors = pd.read_excel(file_path, engine='openpyxl', sheet_name='Directors_Summary')
        
        # Optional sheets (might not exist in older backups)
        try:
            df_petty = pd.read_excel(file_path, engine='openpyxl', sheet_name='Petty_Cash')
        except:
            df_petty = pd.DataFrame()

        try:
            df_tx = pd.read_excel(file_path, engine='openpyxl', sheet_name='Transactions')
        except:
            df_tx = pd.DataFrame() # Fixed empty dataframe assignment

        try:
            df_banks = pd.read_excel(file_path, engine='openpyxl', sheet_name='Banks')
        except:
            df_banks = pd.DataFrame()

        try:
            df_inst = pd.read_excel(file_path, engine='openpyxl', sheet_name='Installments_Summary')
        except:
            df_inst = pd.DataFrame()

        try:
            df_pay = pd.read_excel(file_path, engine='openpyxl', sheet_name='Payment_Breakdown')
        except:
            df_pay = pd.DataFrame()

        # Wipe Database
        InstallmentPaymentEntry.query.delete()
        InstallmentPayment.query.delete()
        Installment.query.delete()
        BankTransaction.query.delete()
        Bank.query.delete()
        PettyCash.query.delete()
        Transaction.query.delete()
        Customer.query.delete()
        Director.query.delete()
        db.session.commit()
        
        # Restore Directors
        director_map = {} # Name -> ID
        for _, row in df_directors.iterrows():
            d = Director(
                name=row['Share name'],
                total_share=row['Total share'],
                per_share_value=row['Per share value'],
                fair_cost=row['Fair Cost'],
                land_value_extra_share=row['Land value of Extra share'],
                total_paid=row['Total Paid Until date'],
                payment_history=str(row['Date & Deposit']) if pd.notna(row['Date & Deposit']) else '',
                bank_name=str(row['B.Name']) if pd.notna(row['B.Name']) else ''
            )
            db.session.add(d)
            db.session.flush() # Get ID
            director_map[d.name] = d.id
            
        # Restore Customers
        customer_map = {} # Customer ID (str) -> DB ID
        for _, row in df_master.iterrows():
            dir_name = row['Director Name']
            if dir_name in director_map:
                c = Customer(
                    director_id=director_map[dir_name],
                    customer_id=row['Customer ID'],
                    name=row['Customer Name'],
                    phone=str(row['Phone']),
                    plot_no=row['Plot No'],
                    total_price=row['Total Price'],
                    down_payment=row['Down Payment'],
                    monthly_installment=row['Monthly Installment'],
                    total_paid=row['Total Paid'],
                    due_amount=row['Due Amount']
                )
                db.session.add(c)
                db.session.flush()
                customer_map[c.customer_id] = c.id
        
        # Restore Transactions
        if not df_tx.empty:
            for _, row in df_tx.iterrows():
                cust_str_id = row['Customer ID']
                if cust_str_id in customer_map:
                    t = Transaction(
                        date=str(row['Date']), # Ensure string
                        amount=row['Amount'],
                        installment_type=row['Installment Type'],
                        bank_name=str(row['Bank Name']) if pd.notna(row['Bank Name']) else '',
                        transaction_id=str(row['Transaction ID']) if pd.notna(row['Transaction ID']) else '',
                        remarks=str(row['Remarks']) if pd.notna(row['Remarks']) else '',
                        images=str(row['Images']) if pd.notna(row['Images']) else '',
                        customer_id=customer_map[cust_str_id]
                    )
                    db.session.add(t)

        # Restore Petty Cash
        if not df_petty.empty:
            for _, row in df_petty.iterrows():
                pc = PettyCash(
                    date=str(row['Date']),
                    description=row['Description'],
                    category=row['Category'],
                    type=row['Type'],
                    amount=row['Income'] if row['Type'] == 'Income' else row['Expense'],
                    images=str(row['Images']) if pd.notna(row['Images']) else ''
                )
                db.session.add(pc)

        # Restore Banks
        bank_map = {} # Account No -> ID
        if not df_banks.empty:
            for _, row in df_banks.iterrows():
                b = Bank(
                    bank_name=row['Bank Name'],
                    branch=str(row['Branch']) if pd.notna(row['Branch']) else '',
                    account_holder_name=str(row['Account Holder']) if pd.notna(row['Account Holder']) else '',
                    joint_name=str(row['Joint Name']) if pd.notna(row['Joint Name']) else '',
                    fhp=str(row['FHP']) if pd.notna(row['FHP']) else '',
                    address=str(row['Address']) if pd.notna(row['Address']) else '',
                    city=str(row['City']) if pd.notna(row['City']) else '',
                    phone=str(row['Phone']) if pd.notna(row['Phone']) else '',
                    customer_id=str(row['Customer ID']) if pd.notna(row['Customer ID']) else '',
                    account_no=str(row['Account No']),
                    prev_account_no=str(row['Prev Account No']) if pd.notna(row['Prev Account No']) else '',
                    account_type=str(row['Account Type']) if pd.notna(row['Account Type']) else '',
                    currency=str(row['Currency']) if pd.notna(row['Currency']) else '',
                    status=str(row['Status']) if pd.notna(row['Status']) else 'Active'
                )
                db.session.add(b)
                db.session.flush()
                bank_map[b.account_no] = b.id

        # Restore Bank Transactions
        if not df_bank_tx.empty:
            for _, row in df_bank_tx.iterrows():
                acc_no = str(row['Bank Account No'])
                if acc_no in bank_map:
                    btx = BankTransaction(
                        date=str(row['Date']),
                        cheque_no=str(row['Cheque No']) if pd.notna(row['Cheque No']) else '',
                        ref_no=str(row['Ref No']) if pd.notna(row['Ref No']) else '',
                        narration=str(row['Narration']) if pd.notna(row['Narration']) else '',
                        transaction_details=str(row['Transaction Details']) if pd.notna(row['Transaction Details']) else '',
                        debit=row['Debit'],
                        credit=row['Credit'],
                        balance=row['Balance'],
                        bank_id=bank_map[acc_no]
                    )
                    db.session.add(btx)

        # Restore Installments
        inst_map = {} # Name -> ID
        if not df_inst.empty:
            for _, row in df_inst.iterrows():
                inst = Installment(
                    name=row['Installment Name'],
                    per_share_amount=row['Per Share Amount'],
                    created_date=str(row['Date Created']) if pd.notna(row['Date Created']) else ''
                )
                db.session.add(inst)
                db.session.flush()
                inst_map[inst.name] = inst.id

        # Restore Installment Payment Entries
        if not df_pay.empty:
            for _, row in df_pay.iterrows():
                inst_name = row['Installment Name']
                cust_str_id = row['Customer ID']
                
                if inst_name in inst_map and cust_str_id in customer_map:
                    iid = inst_map[inst_name]
                    cid = customer_map[cust_str_id]
                    
                    # Ensure InstallmentPayment link exists
                    ip = InstallmentPayment.query.filter_by(installment_id=iid, customer_id=cid).first()
                    if not ip:
                        customer = Customer.query.get(cid)
                        installment = Installment.query.get(iid)
                        ip = InstallmentPayment(
                            installment_id=iid,
                            customer_id=cid,
                            total_due=customer.num_shares * installment.per_share_amount,
                            remaining_due=customer.num_shares * installment.per_share_amount
                        )
                        db.session.add(ip)
                        db.session.flush()
                    
                    ipe = InstallmentPaymentEntry(
                        installment_payment_id=ip.id,
                        date=str(row['Date']),
                        paid_amount=row['Paid Amount'],
                        bank_name=str(row['Destination']) if pd.notna(row['Destination']) else '',
                        transaction_id=str(row['Transaction ID']) if pd.notna(row['Transaction ID']) else '',
                        remarks=str(row['Remarks']) if pd.notna(row['Remarks']) else ''
                    )
                    db.session.add(ipe)
                    
                    # Update IP totals
                    ip.total_paid_for_installment += ipe.paid_amount
                    ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)
                    # Sync the ipe remaining_due as well
                    ipe.remaining_due = ip.remaining_due

        db.session.commit()
        return True, "Data successfully restored."
        
    except Exception as e:
        db.session.rollback()
        print(f"Restore failed: {e}")
        return False, str(e)
