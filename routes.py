from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_from_directory, session
from werkzeug.utils import secure_filename
import os
import threading
import time
from models import Director, Customer, Transaction, PettyCash, Bank, BankTransaction, Installment, InstallmentPayment, InstallmentPaymentEntry, PettyCashCategory
from database import db
from logic import sync_to_excel, restore_from_excel
import pandas as pd
import io
from datetime import datetime, timedelta
from sqlalchemy import func
from flask import send_file
import random
import string
import json
from telegram_utils import send_telegram_message, send_telegram_document
from profile_manager import load_profiles, add_profile, switch_profile, delete_profile, get_base_data_path

def backup_to_telegram(action_name="Database Update"):
    """
    Helper to send the current database to Telegram.
    Runs in background thread — does NOT block the user response.
    """
    try:
        db_path = current_app.config.get('DATABASE_PATH')
        if db_path and os.path.exists(db_path):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            caption = f"Backup triggered by: {action_name}\nTime: {timestamp}\nSent from {current_app.config['COMPANY_NAME']}"
            send_telegram_document(db_path, caption=caption)
    except Exception as e:
        print(f"Backup failed: {e}")

def _sync_all():
    """Runs both excel sync and an excel backup in background."""
    try:
        sync_to_excel()
    except Exception as e:
        print(f"Background sync_to_excel error: {e}")


# ============================================================
# --- Background Task Helper ---
# ============================================================
def _run_with_context(app, func, *args, **kwargs):
    """Run a function inside a Flask app context in a background thread."""
    with app.app_context():
        try:
            func(*args, **kwargs)
        except Exception as e:
            print(f"Background task error ({func.__name__}): {e}")

def run_background(func, *args, **kwargs):
    """Fire-and-forget: run func in a daemon background thread."""
    app = current_app._get_current_object()
    t = threading.Thread(target=_run_with_context, args=(app, func) + args, kwargs=kwargs, daemon=True)
    t.start()

main = Blueprint('main', __name__)

@main.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# --- Profile Management Routes ---
@main.route('/profiles')
def list_profiles():
    config = load_profiles()
    return render_template('profiles.html', 
                         profiles=config['profiles'], 
                         active_profile=config['active_profile'])

@main.route('/profile/add', methods=['POST'])
def add_new_profile():
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_profiles'))

    name = request.form.get('name')
    if not name:
        flash('Company Name is required.', 'warning')
        return redirect(url_for('main.list_profiles'))
    
    # Generate ID from name
    profile_id = secure_filename(name).lower()
    
    # Check if exists
    config = load_profiles()
    if profile_id in config['profiles']:
        profile_id = f"{profile_id}_{int(datetime.now().timestamp())}"

    # Set data path: Subfolder in the base app data path
    base_path = get_base_data_path()
    profile_path = os.path.join(base_path, profile_id)
    os.makedirs(profile_path, exist_ok=True)
    
    add_profile(profile_id, name, profile_path)
    flash(f'Company "{name}" added successfully!', 'success')
    return redirect(url_for('main.list_profiles'))

@main.route('/profile/switch/<profile_id>')
def switch_company(profile_id):
    config_data = load_profiles()
    if profile_id in config_data['profiles']:
        if switch_profile(profile_id):
            profile = config_data['profiles'][profile_id]
            
            # --- Robust Instant Switch Logic ---
            from sqlalchemy import create_engine
            
            # 1. Update app config
            db_path = os.path.join(profile['path'], 'project.db')
            current_app.config['COMPANY_NAME'] = profile['name']
            current_app.config['PROFILE_ID'] = profile_id
            current_app.config['DATABASE_PATH'] = db_path
            current_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
            
            # 2. Reset Session
            db.session.remove()
            
            # 3. Swap Engine in Flask-SQLAlchemy State
            try:
                state = current_app.extensions['sqlalchemy']
                # Dispose old engine
                old_engine = state.engines.get(None)
                if old_engine:
                    old_engine.dispose()
                
                # Create and bind new engine
                new_uri = current_app.config['SQLALCHEMY_DATABASE_URI']
                state.engines[None] = create_engine(new_uri)
                
                # IMPORTANT: Initialize tables in the new database if they don't exist
                # This fixes the "no such table" error for brand new profiles
                with current_app.app_context():
                    db.create_all()
                
                print(f"Instantly switched to DB: {db_path}")
            except Exception as e:
                print(f"Critical error during instant switch: {e}")
                flash(f'Error switching database instantly. Please restart the application.', 'danger')
                return redirect(url_for('main.list_profiles'))

            flash(f'Successfully switched to "{profile["name"]}" project instantly!', 'success')
            return redirect(url_for('main.index'))
    
    flash('Failed to switch company.', 'danger')
    return redirect(url_for('main.list_profiles'))

@main.route('/profile/delete/<profile_id>', methods=['POST'])
def remove_profile(profile_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_profiles'))

    if delete_profile(profile_id):
        flash('Company removed.', 'info')
    else:
        flash('Cannot remove the default or active company.', 'warning')
    return redirect(url_for('main.list_profiles'))


@main.route('/profile/rename/<profile_id>', methods=['POST'])
def rename_profile(profile_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_profiles'))

    new_name = request.form.get('new_name', '').strip()
    if not new_name:
        flash('Name cannot be empty.', 'warning')
        return redirect(url_for('main.list_profiles'))

    from profile_manager import load_profiles, save_profiles
    config = load_profiles()
    if profile_id in config['profiles']:
        config['profiles'][profile_id]['name'] = new_name
        save_profiles(config)
        # Update live app config if this is the active profile
        if config.get('active_profile') == profile_id:
            current_app.config['COMPANY_NAME'] = new_name
        flash(f'Project renamed to "{new_name}" successfully!', 'success')
    else:
        flash('Profile not found.', 'danger')
    return redirect(url_for('main.list_profiles'))


@main.route('/profile/reset/<profile_id>', methods=['POST'])
def reset_profile_data(profile_id):
    """Wipe all data rows for a project while keeping the schema."""
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_profiles'))

    from profile_manager import load_profiles
    from sqlalchemy import create_engine, text as sa_text, inspect as sa_inspect
    import shutil

    config = load_profiles()
    if profile_id not in config['profiles']:
        flash('Profile not found.', 'danger')
        return redirect(url_for('main.list_profiles'))

    profile = config['profiles'][profile_id]
    db_path = os.path.join(profile['path'], 'project.db')

    try:
        engine = create_engine(f'sqlite:///{db_path}')
        with engine.connect() as conn:
            insp = sa_inspect(engine)
            tables = insp.get_table_names()
            conn.execute(sa_text('PRAGMA foreign_keys = OFF'))
            for table in tables:
                conn.execute(sa_text(f'DELETE FROM "{table}"'))
            conn.execute(sa_text('PRAGMA foreign_keys = ON'))
            conn.commit()
        engine.dispose()

        # Also clear uploads for this profile
        uploads_dir = os.path.join(profile['path'], 'uploads')
        if os.path.exists(uploads_dir):
            shutil.rmtree(uploads_dir)
            os.makedirs(uploads_dir, exist_ok=True)

        # If resetting active profile, reload session
        if config.get('active_profile') == profile_id:
            db.session.remove()

        flash(f'All data for "{profile["name"]}" has been wiped. Schema kept intact.', 'warning')
    except Exception as e:
        flash(f'Reset failed: {e}', 'danger')

    return redirect(url_for('main.list_profiles'))


@main.route('/profile/delete-full/<profile_id>', methods=['POST'])
def delete_profile_full(profile_id):
    """Delete a project profile AND its entire data folder from disk."""
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_profiles'))

    from profile_manager import load_profiles, save_profiles
    import shutil

    config = load_profiles()
    if profile_id not in config['profiles']:
        flash('Profile not found.', 'danger')
        return redirect(url_for('main.list_profiles'))

    if profile_id == 'default':
        flash('Cannot delete the default project.', 'warning')
        return redirect(url_for('main.list_profiles'))

    active_id = config.get('active_profile', 'default')
    if profile_id == active_id:
        flash('Cannot delete the currently active project. Switch to another first.', 'danger')
        return redirect(url_for('main.list_profiles'))

    profile = config['profiles'][profile_id]
    profile_path = profile.get('path')

    try:
        # Remove folder from disk
        if profile_path and os.path.exists(profile_path):
            shutil.rmtree(profile_path)

        # Remove from config
        del config['profiles'][profile_id]
        save_profiles(config)

        flash(f'Project "{profile["name"]}" and all its data have been permanently deleted.', 'warning')
    except Exception as e:
        flash(f'Delete failed: {e}', 'danger')

    return redirect(url_for('main.list_profiles'))


@main.route('/')
def index():
    directors = Director.query.all()
    installments = Installment.query.all()
    total_per_share_installment = sum((inst.per_share_amount or 0.0) for inst in installments)
    
    # Calculate Liquidity
    # Cash in hand
    cash_income = db.session.query(func.sum(PettyCash.amount)).filter(PettyCash.type == 'Income').scalar() or 0
    cash_expense = db.session.query(func.sum(PettyCash.amount)).filter(PettyCash.type == 'Expense').scalar() or 0
    cash_in_hand = cash_income - cash_expense
    
    # Bank Balance
    bank_credit = db.session.query(func.sum(BankTransaction.credit)).scalar() or 0
    bank_debit = db.session.query(func.sum(BankTransaction.debit)).scalar() or 0
    bank_balance = bank_credit - bank_debit
    
    total_liquidity = cash_in_hand + bank_balance
    
    # Calculate Grand Totals (Installment Based)
    total_payable = 0
    total_paid = 0
    total_due = 0
    
    for d in directors:
        # Commitments are based on Director.total_share, NOT the sum of their customers' shares
        d_payable = (d.total_share or 0.0) * total_per_share_installment
        d_paid = sum((c.total_paid or 0.0) for c in d.customers)
        
        # Attach computed values to director object for template use
        d.computed_payable = d_payable
        d.computed_paid = d_paid
        d.computed_due = d_payable - d_paid
        
        total_payable += d_payable
        total_paid += d_paid
        total_due += d.computed_due
        
    # Prepare Graph Data (Last 6 Months)
    graph_labels = []
    graph_income = []
    graph_expense = []
    
    today = datetime.now()
    # Go back 6 months from current month
    for i in range(5, -1, -1):
        # Calculate year and month
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
            
        m_str = f"{year}-{month:02d}"
        month_name = datetime(year, month, 1).strftime('%b %Y')
        graph_labels.append(month_name)
        
        # Income for this month (Cash + Bank)
        p_income = db.session.query(func.sum(PettyCash.amount)).filter(
            PettyCash.type == 'Income', 
            PettyCash.date.like(f'{m_str}%')
        ).scalar() or 0
        b_income = db.session.query(func.sum(BankTransaction.credit)).filter(
            BankTransaction.date.like(f'{m_str}%')
        ).scalar() or 0
        graph_income.append(p_income + b_income)
        
        # Expense for this month (Cash + Bank)
        p_expense = db.session.query(func.sum(PettyCash.amount)).filter(
            PettyCash.type == 'Expense', 
            PettyCash.date.like(f'{m_str}%')
        ).scalar() or 0
        b_expense = db.session.query(func.sum(BankTransaction.debit)).filter(
            BankTransaction.date.like(f'{m_str}%')
        ).scalar() or 0
        graph_expense.append(p_expense + b_expense)

    return render_template('index.html', 
                         directors=directors,
                         total_per_share_installment=total_per_share_installment,
                         grand_total_payable=total_payable,
                         grand_total_paid=total_paid,
                         grand_total_due=total_due,
                         cash_in_hand=cash_in_hand,
                         bank_balance=bank_balance,
                         total_liquidity=total_liquidity,
                         graph_labels=graph_labels,
                         graph_income=graph_income,
                         graph_expense=graph_expense,
                         now=datetime.now())

def verify_password():
    # --- Session-based auth: skip modal if verified within last 30 minutes ---
    auth_time = session.get('admin_auth_time', 0)
    if time.time() - auth_time < 1800:  # 30 minutes
        return True

    password = request.form.get('admin_password')
    admin_pass = current_app.config.get('ADMIN_PASSWORD')
    if password == admin_pass:
        session['admin_auth_time'] = time.time()
        return True
    return False


@main.route('/auth/check')
def check_auth_status():
    """Returns JSON indicating if the current session has a valid admin auth token."""
    auth_time = session.get('admin_auth_time', 0)
    valid = (time.time() - auth_time < 1800)
    from flask import jsonify
    return jsonify({'valid': valid})


@main.route('/auth/logout-session', methods=['POST'])
def logout_session():
    """Clears the admin session token (manual lock)."""
    session.pop('admin_auth_time', None)
    flash('Session locked.', 'info')
    return redirect(url_for('main.index'))

# --- OTP Store (In-Memory for simplicity) ---
otp_store = {}

@main.route('/change_password_request', methods=['GET', 'POST'])
def change_password_request():
    if request.method == 'POST':
        # Generate OTP
        otp = ''.join(random.choices(string.digits, k=6))
        
        if send_telegram_message(f"Your OTP for Password Change is: {otp}\nSent from {current_app.config['COMPANY_NAME']}"):
            otp_store['current_otp'] = otp
            flash('OTP sent to Telegram!', 'info')
            return redirect(url_for('main.verify_otp_page'))
        else:
            flash('Failed to send OTP. Check internet or bot config.', 'danger')
            
    return render_template('change_password_request.html')

@main.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp_page():
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        new_password = request.form.get('new_password')
        
        if 'current_otp' in otp_store and otp_store['current_otp'] == user_otp:
            # Update Password
            # Use DATA_FOLDER to ensure persistence
            data_folder = current_app.config.get('DATA_FOLDER', current_app.root_path)
            config_path = os.path.join(data_folder, 'admin_config.json')
            
            try:
                with open(config_path, 'w') as f:
                    json.dump({"ADMIN_PASSWORD": new_password}, f)
            except Exception as e:
                flash(f'Error saving password: {e}', 'danger')
                return redirect(url_for('main.index'))
            
            # Update Runtime Config
            current_app.config['ADMIN_PASSWORD'] = new_password
            
            # Clear OTP
            del otp_store['current_otp']
            
            flash('Password Changed Successfully!', 'success')
            return redirect(url_for('main.index'))
        else:
            flash('Invalid OTP!', 'danger')
            
    return render_template('verify_otp.html')


# --- Director Routes ---
@main.route('/director/add', methods=['GET', 'POST'])
def add_director():
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.add_director'))

        name = request.form.get('name')
        phone = request.form.get('phone')
        
        if name:
            new_director = Director(
                name=name, 
                phone=phone,
                bank_name=request.form.get('bank_name'),
                total_share=float(request.form.get('total_share') or 0),
                total_paid=float(request.form.get('total_paid') or 0),
                payment_history=request.form.get('payment_history')
            )
            db.session.add(new_director)
            db.session.commit()
            run_background(backup_to_telegram, "Added Director: " + name + "\nSent from " + current_app.config['COMPANY_NAME'])
            flash('Director added successfully!', 'success')
            return redirect(url_for('main.index'))
    return render_template('director_form.html')

@main.route('/director/edit/<int:id>', methods=['GET', 'POST'])
def edit_director(id):
    director = Director.query.get_or_404(id)
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.edit_director', id=id))

        director.name = request.form.get('name')
        director.phone = request.form.get('phone')
        director.bank_name = request.form.get('bank_name')
        director.total_share = float(request.form.get('total_share') or 0)
        director.total_paid = float(request.form.get('total_paid') or 0)
        director.payment_history = request.form.get('payment_history')
        
        db.session.commit()
        run_background(backup_to_telegram, "Edited Director: " + director.name + "\nSent from " + current_app.config['COMPANY_NAME'])
        flash('Director updated successfully!', 'success')
        return redirect(url_for('main.index'))
    return render_template('director_form.html', director=director)

@main.route('/director/delete/<int:id>', methods=['POST'])
def delete_director(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.index'))

    director = Director.query.get_or_404(id)
    # Optional: logic to handle orphaned customers (delete or unlink). 
    # For now, let's assume we delete them or db cascade (we didn't set cascade).
    # Ideally we should warn user. But per request, simple Remove.
    # Let's delete customers first manually if constraints exist
    for customer in director.customers:
        db.session.delete(customer)
        
    db.session.delete(director)
    db.session.commit()
    run_background(backup_to_telegram, "Deleted Director: " + director.name + "\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Director and their customers removed!', 'info')
    return redirect(url_for('main.index'))


# --- Customer Routes ---
@main.route('/customer/add', methods=['GET', 'POST'])
def add_customer():
    directors = Director.query.all()
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.add_customer'))

        # Extraction
        director_id = request.form.get('director_id')
        customer_id = request.form.get('customer_id')
        name = request.form.get('name')
        phone = request.form.get('phone')
        father_name = request.form.get('father_name')
        mother_name = request.form.get('mother_name')
        dob = request.form.get('dob')
        religion = request.form.get('religion')
        profession = request.form.get('profession')
        nid_no = request.form.get('nid_no')
        present_address = request.form.get('present_address')
        permanent_address = request.form.get('permanent_address')
        plot_no = request.form.get('plot_no')
        total_price = float(request.form.get('total_price') or 0)
        down_payment = float(request.form.get('down_payment') or 0)
        monthly_installment = float(request.form.get('monthly_installment') or 0)
        total_paid = float(request.form.get('total_paid') or 0)
        num_shares = float(request.form.get('num_shares') or 1)
        
        # Calculation
        due_amount = total_price - total_paid
        
        new_customer = Customer(
            director_id=director_id,
            customer_id=customer_id,
            name=name,
            phone=phone,
            father_name=father_name,
            mother_name=mother_name,
            dob=dob,
            religion=religion,
            profession=profession,
            nid_no=nid_no,
            present_address=present_address,
            permanent_address=permanent_address,
            plot_no=plot_no,
            total_price=total_price,
            down_payment=down_payment,
            monthly_installment=monthly_installment,
            total_paid=total_paid,
            due_amount=due_amount,
            num_shares=num_shares
        )
        db.session.add(new_customer)
        db.session.commit()
        run_background(backup_to_telegram, "Added Customer: " + name + "\nSent from " + current_app.config['COMPANY_NAME'])
        flash('Customer added successfully!', 'success')
        return redirect(url_for('main.index'))
        
    return render_template('customer_form.html', directors=directors)

@main.route('/customer/edit/<int:id>', methods=['GET', 'POST'])
def edit_customer(id):
    customer = Customer.query.get_or_404(id)
    directors = Director.query.all()
    
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.edit_customer', id=id))

        customer.director_id = request.form.get('director_id')
        customer.customer_id = request.form.get('customer_id')
        customer.name = request.form.get('name')
        customer.phone = request.form.get('phone')
        customer.father_name = request.form.get('father_name')
        customer.mother_name = request.form.get('mother_name')
        customer.dob = request.form.get('dob')
        customer.religion = request.form.get('religion')
        customer.profession = request.form.get('profession')
        customer.nid_no = request.form.get('nid_no')
        customer.present_address = request.form.get('present_address')
        customer.permanent_address = request.form.get('permanent_address')
        customer.plot_no = request.form.get('plot_no')
        customer.total_price = float(request.form.get('total_price') or 0)
        customer.down_payment = float(request.form.get('down_payment') or 0)
        customer.monthly_installment = float(request.form.get('monthly_installment') or 0)
        customer.total_paid = float(request.form.get('total_paid') or 0)
        customer.num_shares = float(request.form.get('num_shares') or 1)
        
        # Calc Due
        customer.due_amount = customer.total_price - customer.total_paid
        
        db.session.commit()
        run_background(backup_to_telegram, "Edited Customer: " + customer.name + "\nSent from " + current_app.config['COMPANY_NAME'])
        flash('Customer updated successfully!', 'success')
        return redirect(url_for('main.index'))
        
    return render_template('customer_form.html', customer=customer, directors=directors)

@main.route('/delete_customer/<int:id>', methods=['POST'])
def delete_customer(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.index'))

    customer = Customer.query.get_or_404(id)
    db.session.delete(customer)
    db.session.commit()
    # Sync to Excel
    run_background(backup_to_telegram, "Deleted Customer\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Customer deleted!', 'success')
    return redirect(url_for('main.index'))

# --- Transaction Routes ---
@main.route('/manage_transactions/<int:customer_id>', methods=['GET', 'POST'])
def manage_transactions(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    all_milestones = Installment.query.all()
    banks = Bank.query.all()
    categories = PettyCashCategory.query.all()
    
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.manage_transactions', customer_id=customer_id))

        date = request.form.get('date')
        amount = float(request.form.get('amount') or 0)
        installment_type = request.form.get('installment_type') # Could be legacy or a dynamic installment name
        payment_source = request.form.get('payment_source') # Cash or Bank
        bank_id = request.form.get('bank_id')
        petty_category = request.form.get('petty_category')
        transaction_id = request.form.get('transaction_id')
        remarks = request.form.get('remarks')
        
        # Handle Images
        images = []
        if 'evidence' in request.files:
            files = request.files.getlist('evidence')
            for file in files:
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    images.append(filename)
        image_str = ','.join(images)

        # 1. Determine if this is a Dynamic Installment or Legacy
        dynamic_inst = Installment.query.filter_by(name=installment_type).first()
        
        if dynamic_inst:
            # Handle as InstallmentPayment
            ip = InstallmentPayment.query.filter_by(installment_id=dynamic_inst.id, customer_id=customer.id).first()
            if not ip:
                # Should normally exist, but let's be safe
                ip = InstallmentPayment(
                    installment_id=dynamic_inst.id,
                    customer_id=customer.id,
                    total_due=customer.num_shares * dynamic_inst.per_share_amount,
                    remaining_due=customer.num_shares * dynamic_inst.per_share_amount
                )
                db.session.add(ip)
                db.session.flush()

            entry = InstallmentPaymentEntry(
                installment_payment_id=ip.id,
                date=date,
                paid_amount=amount,
                remaining_due=max(0.0, ip.remaining_due - amount),
                bank_name=payment_source if payment_source == 'Cash' else f"Bank ID: {bank_id}",
                transaction_id=transaction_id,
                remarks=remarks,
                images=image_str
            )
            db.session.add(entry)
            
            ip.total_paid_for_installment += amount
            ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)
            description = f"Installment: {dynamic_inst.name} - {customer.name}"
        else:
            # Handle as Legacy Transaction
            new_tx = Transaction(
                date=date,
                amount=amount,
                installment_type=installment_type,
                bank_name=payment_source if payment_source == 'Cash' else f"Bank ID: {bank_id}",
                transaction_id=transaction_id,
                remarks=remarks,
                images=image_str,
                customer=customer
            )
            db.session.add(new_tx)
            description = f"{installment_type} - {customer.name}"

        # 2. Integrated Ledger Updates
        if payment_source == 'Cash':
            new_petty = PettyCash(
                date=date,
                description=description,
                category=petty_category or ("Installment" if dynamic_inst else "General"),
                type="Income",
                amount=amount,
                images=image_str
            )
            db.session.add(new_petty)
        elif payment_source == 'Bank' and bank_id:
            bank = Bank.query.get(bank_id)
            if bank:
                new_bank_tx = BankTransaction(
                    bank_id=bank.id,
                    date=date,
                    cheque_no=transaction_id,
                    narration=description,
                    transaction_details=f"Payment from {customer.name}",
                    credit=amount,
                    balance=0.0
                )
                db.session.add(new_bank_tx)
                db.session.flush()
                recompute_bank_balances(bank.id)

        db.session.commit()
        
        # 3. Recalculate everything
        recalculate_director_totals(customer.director_id)
        run_background(backup_to_telegram, f"Recorded {installment_type} for {customer.name}")
        flash('Payment recorded successfully!', 'success')
        return redirect(url_for('main.manage_transactions', customer_id=customer_id))
        
    # GET: Prepare Combined History
    legacy_txs = Transaction.query.filter_by(customer_id=customer_id).all()
    inst_payments = InstallmentPayment.query.filter_by(customer_id=customer_id).all()
    
    combined_history = []
    # Add legacy
    for tx in legacy_txs:
        combined_history.append({
            'type': 'legacy',
            'id': tx.id,
            'date': tx.date,
            'amount': tx.amount,
            'label': tx.installment_type,
            'method': tx.bank_name,
            'txid': tx.transaction_id,
            'remarks': tx.remarks,
            'images': tx.images
        })
    # Add installment entries
    for ip in inst_payments:
        for entry in ip.entries:
            combined_history.append({
                'type': 'installment',
                'id': entry.id,
                'date': entry.date,
                'amount': entry.paid_amount,
                'label': ip.installment.name,
                'method': entry.bank_name,
                'txid': entry.transaction_id,
                'remarks': entry.remarks,
                'images': entry.images
            })
    
    # Sort by date desc (simple string sort usually works if YYYY-MM-DD)
    combined_history.sort(key=lambda x: x['date'], reverse=True)

    return render_template('customer_transactions.html', 
                         customer=customer, 
                         history=combined_history, 
                         all_milestones=all_milestones,
                         banks=banks,
                         categories=categories)

@main.route('/delete_transaction/<int:id>', methods=['POST'])
def delete_transaction(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        # We need customer_id to redirect, but we don't have it easily without querying tx first
        # But we can query tx first
        tx = Transaction.query.get(id)
        if tx:
             return redirect(url_for('main.manage_transactions', customer_id=tx.customer_id))
        return redirect(url_for('main.index'))

    tx = Transaction.query.get_or_404(id)
    customer_id = tx.customer_id
    customer = Customer.query.get(customer_id)
    
    # Revert Customer Totals
    customer.total_paid -= tx.amount
    customer.due_amount = customer.total_price - customer.total_paid
    
    db.session.delete(tx)
    db.session.commit()
    run_background(backup_to_telegram, "Deleted Transaction\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Transaction Deleted!', 'warning')
    return redirect(url_for('main.manage_transactions', customer_id=customer_id))

@main.route('/transaction/edit/<int:id>', methods=['POST'])
def edit_transaction_details(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        # We need customer_id to redirect
        tx = Transaction.query.get(id)
        if tx:
            # Revert to manage page if possible
            return redirect(url_for('main.manage_transactions', customer_id=tx.customer_id))
        return redirect(url_for('main.index'))

    tx = Transaction.query.get_or_404(id)
    customer = Customer.query.get(tx.customer_id)
    
    # 1. Revert Old Amount from Customer Totals
    customer.total_paid -= tx.amount
    
    # 2. Update Transaction Data
    tx.date = request.form.get('date')
    tx.amount = float(request.form.get('amount') or 0)
    tx.installment_type = request.form.get('installment_type')
    tx.bank_name = request.form.get('bank_name')
    tx.transaction_id = request.form.get('transaction_id')
    tx.remarks = request.form.get('remarks')
    
    # 3. Handle New Images (Append)
    if 'evidence' in request.files:
        files = request.files.getlist('evidence')
        new_images = []
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                new_images.append(filename)
        
        if new_images:
            current_images = tx.images.split(',') if tx.images else []
            updated_images = current_images + new_images
            tx.images = ','.join(updated_images)
            
    # 4. Apply New Amount to Customer Totals
    customer.total_paid += tx.amount
    customer.due_amount = customer.total_price - customer.total_paid
    
    db.session.commit()
    run_background(backup_to_telegram, "Edited Transaction\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Transaction Updated!', 'success')
    return redirect(url_for('main.manage_transactions', customer_id=customer.id))
    
@main.route('/delete_installment_entry/<int:id>', methods=['POST'])
def delete_installment_entry(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        entry = InstallmentPaymentEntry.query.get(id)
        if entry:
            return redirect(url_for('main.manage_transactions', customer_id=entry.installment_payment.customer_id))
        return redirect(url_for('main.index'))

    entry = InstallmentPaymentEntry.query.get_or_404(id)
    ip = entry.installment_payment
    customer = ip.customer
    
    # Revert Totals
    amount = entry.paid_amount
    ip.total_paid_for_installment -= amount
    ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)
    
    customer.total_paid -= amount
    customer.due_amount = customer.total_price - customer.total_paid
    
    db.session.delete(entry)
    db.session.commit()
    
    recalculate_director_totals(customer.director_id)
    run_background(backup_to_telegram, f"Deleted Installment Entry for {customer.name}\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Installment Payment Deleted!', 'warning')
    return redirect(url_for('main.manage_transactions', customer_id=customer.id))

@main.route('/installment_entry/edit/<int:id>', methods=['POST'])
def edit_installment_entry(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        entry = InstallmentPaymentEntry.query.get(id)
        if entry:
            return redirect(url_for('main.manage_transactions', customer_id=entry.installment_payment.customer_id))
        return redirect(url_for('main.index'))

    entry = InstallmentPaymentEntry.query.get_or_404(id)
    ip = entry.installment_payment
    customer = ip.customer
    
    # 1. Revert Old Amount
    old_amount = entry.paid_amount
    ip.total_paid_for_installment -= old_amount
    customer.total_paid -= old_amount
    
    # 2. Update Data
    new_amount = float(request.form.get('amount') or 0)
    entry.date = request.form.get('date')
    entry.paid_amount = new_amount
    entry.transaction_id = request.form.get('transaction_id')
    entry.remarks = request.form.get('remarks')
    # Handle Source
    payment_source = request.form.get('payment_source')
    bank_id = request.form.get('bank_id')
    entry.bank_name = payment_source if payment_source == 'Cash' else f"Bank ID: {bank_id}"
    
    # Handle Images
    if 'evidence' in request.files:
        files = request.files.getlist('evidence')
        new_images = []
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                new_images.append(filename)
        
        if new_images:
            current_images = entry.images.split(',') if entry.images else []
            updated_images = current_images + new_images
            entry.images = ','.join(updated_images)
            
    # 3. Apply New Totals
    ip.total_paid_for_installment += new_amount
    ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)
    
    customer.total_paid += new_amount
    customer.due_amount = customer.total_price - customer.total_paid
    
    db.session.commit()
    recalculate_director_totals(customer.director_id)
    run_background(backup_to_telegram, f"Edited Installment Entry for {customer.name}\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Installment Payment Updated!', 'success')
    return redirect(url_for('main.manage_transactions', customer_id=customer.id))

# --- Petty Cash Routes ---
@main.route('/petty_cash', methods=['GET', 'POST'])
def manage_petty_cash():
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.manage_petty_cash'))

        date = request.form.get('date')
        description = request.form.get('description')
        category = request.form.get('category') or 'Uncategorized'
        type = request.form.get('type')
        amount = float(request.form.get('amount') or 0)
        
        # Handle Evidence Files
        images = []
        if 'evidence' in request.files:
            files = request.files.getlist('evidence')
            for file in files:
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    # Optional: Add timestamp/UUID to filename to prevent collisions?
                    # For now keeping it simple as per previous pattern
                    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    images.append(filename)
        
        new_entry = PettyCash(
            date=date,
            description=description,
            category=category,
            type=type,
            amount=amount,
            images=','.join(images)
        )
        db.session.add(new_entry)
        db.session.commit()
        run_background(backup_to_telegram, "Added Petty Cash: " + description + "\nSent from " + current_app.config['COMPANY_NAME'])
        flash('Petty Cash Entry Added!', 'success')
        return redirect(url_for('main.manage_petty_cash'))
        
    # Filter Logic
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    filter_category = request.args.get('category')
    
    query = PettyCash.query
    
    if start_date:
        query = query.filter(PettyCash.date >= start_date)
    if end_date:
        query = query.filter(PettyCash.date <= end_date)
    if filter_category and filter_category != 'All':
        query = query.filter(PettyCash.category == filter_category)
        
    entries = query.order_by(PettyCash.date.desc()).all()
    
    total_income = sum(e.amount for e in entries if e.type == 'Income')
    total_expense = sum(e.amount for e in entries if e.type == 'Expense')
    current_balance = total_income - total_expense
    
    categories = PettyCashCategory.query.order_by(PettyCashCategory.name).all()
    
    return render_template('petty_cash.html', entries=entries, 
                         total_income=total_income, 
                         total_expense=total_expense, 
                         current_balance=current_balance,
                         categories=categories)

@main.route('/petty_cash/export')
def export_petty_cash_report():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    filter_category = request.args.get('category')
    
    query = PettyCash.query
    
    if start_date:
        query = query.filter(PettyCash.date >= start_date)
    if end_date:
        query = query.filter(PettyCash.date <= end_date)
    if filter_category and filter_category != 'All':
        query = query.filter(PettyCash.category == filter_category)
        
    entries = query.order_by(PettyCash.date.desc()).all()
    
    data = []
    for e in entries:
        data.append({
            'Date': e.date,
            'Description': e.description,
            'Category': e.category,
            'Type': e.type,
            'Income': e.amount if e.type == 'Income' else 0,
            'Expense': e.amount if e.type == 'Expense' else 0,
            'Images': e.images
        })
        
    df = pd.DataFrame(data)
    
    # Calculate totals for the report
    total_income = df['Income'].sum() if not df.empty else 0
    total_expense = df['Expense'].sum() if not df.empty else 0
    
    # Append Total Row
    if not df.empty:
        df.loc['Total'] = pd.Series(dtype='float64')
        df.at['Total', 'Description'] = 'TOTAL'
        df.at['Total', 'Income'] = total_income
        df.at['Total', 'Expense'] = total_expense
        df.at['Total', 'Category'] = f'Balance: {total_income - total_expense}'
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Petty_Cash_Report', index=False)
        # Polish width
        worksheet = writer.sheets['Petty_Cash_Report']
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

    output.seek(0)
    return send_file(output, download_name="Petty_Cash_Report.xlsx", as_attachment=True)

@main.route('/delete_petty_cash/<int:id>', methods=['POST'])
def delete_petty_cash(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.manage_petty_cash'))

    entry = PettyCash.query.get_or_404(id)
    db.session.delete(entry)
    db.session.commit()
    run_background(backup_to_telegram, "Deleted Petty Cash\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Entry Deleted!', 'info')
    return redirect(url_for('main.manage_petty_cash'))

@main.route('/petty_cash/invoice/<int:id>')
def invoice_view(id):
    entry = PettyCash.query.get_or_404(id)
    return render_template('invoice.html', entry=entry, now=datetime.now())

@main.route('/petty_cash/edit/<int:id>', methods=['POST'])
def edit_petty_cash(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.manage_petty_cash'))

    entry = PettyCash.query.get_or_404(id)
    
    entry.date = request.form.get('date')
    entry.description = request.form.get('description')
    entry.category = request.form.get('category') or 'Uncategorized'
    entry.type = request.form.get('type')
    entry.amount = float(request.form.get('amount') or 0)
    
    # Handle New Evidence Files (Append to existing?)
    # For simplicity, if new files are uploaded, we append them.
    if 'evidence' in request.files:
        files = request.files.getlist('evidence')
        new_images = []
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                new_images.append(filename)
        
        if new_images:
            current_images = entry.images.split(',') if entry.images else []
            updated_images = current_images + new_images
            entry.images = ','.join(updated_images)
            
    db.session.commit()
    run_background(backup_to_telegram, "Edited Petty Cash\nSent from " + current_app.config['COMPANY_NAME'])
    flash('Entry Updated Successfully!', 'success')
    return redirect(url_for('main.manage_petty_cash'))

@main.route('/report/download')
def download_report():
    # Generate a summary report
    # Group by Director, Sum Collection (Total Paid), Outstanding (Due Amount)
    
    directors = Director.query.all()
    report_data = []
    
    grand_total_collection = 0
    grand_total_due = 0
    
    for d in directors:
        d_collection = sum(c.total_paid for c in d.customers)
        d_due = sum(c.due_amount for c in d.customers)
        
        grand_total_collection += d_collection
        grand_total_due += d_due
        
        report_data.append({
            'Director': d.name,
            'Total Collection': d_collection,
            'Total Outstanding Dues': d_due
        })
        
    # Append Grand Total
    report_data.append({
        'Director': 'GRAND TOTAL',
        'Total Collection': grand_total_collection,
        'Total Outstanding Dues': grand_total_due
    })
    
    df = pd.DataFrame(report_data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Summary_Report', index=False)
        
        # Polish width
        worksheet = writer.sheets['Summary_Report']
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

    output.seek(0)
    
    return send_file(output, download_name="Summary_Report.xlsx", as_attachment=True)

# --- Excel Helper ---
def format_excel_width(writer, sheet_name):
    """Helper to auto-adjust column width for Excel sheets."""
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

# --- New Customer Reports ---

@main.route('/report/customers/all')
def download_all_customers_report():
    customers = Customer.query.join(Director).all()
    
    data = []
    for c in customers:
        data.append({
            'Director Name': c.director.name,
            'Customer ID': c.customer_id,
            'Customer Name': c.name,
            'Phone': c.phone,
            'Father Name': c.father_name,
            'Mother Name': c.mother_name,
            'DOB': c.dob,
            'Religion': c.religion,
            'Profession': c.profession,
            'NID No': c.nid_no,
            'Present Address': c.present_address,
            'Permanent Address': c.permanent_address,
            'Plot No': c.plot_no,
            'Total Price': c.total_price,
            'Down Payment': c.down_payment,
            'Monthly Installment': c.monthly_installment,
            'Total Paid': c.total_paid,
            'Due Amount': c.due_amount
        })
        
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='All_Customers', index=False)
        format_excel_width(writer, 'All_Customers')
        
    output.seek(0)
    return send_file(output, download_name="All_Customers.xlsx", as_attachment=True)

@main.route('/report/director/<int:id>/customers')
def download_director_customers_report(id):
    director = Director.query.get_or_404(id)
    customers = director.customers
    
    data = []
    for c in customers:
        data.append({
            'Director Name': director.name,
            'Customer ID': c.customer_id,
            'Customer Name': c.name,
            'Phone': c.phone,
            'Father Name': c.father_name,
            'Mother Name': c.mother_name,
            'DOB': c.dob,
            'Religion': c.religion,
            'Profession': c.profession,
            'NID No': c.nid_no,
            'Present Address': c.present_address,
            'Permanent Address': c.permanent_address,
            'Plot No': c.plot_no,
            'Total Price': c.total_price,
            'Down Payment': c.down_payment,
            'Monthly Installment': c.monthly_installment,
            'Total Paid': c.total_paid,
            'Due Amount': c.due_amount
        })
        
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet_name = 'Customers'
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        format_excel_width(writer, sheet_name)
        
    output.seek(0)
    filename = f"Director_{secure_filename(director.name)}_Customers.xlsx"
    return send_file(output, download_name=filename, as_attachment=True)

@main.route('/report/customer/<int:id>')
def download_individual_customer_report(id):
    c = Customer.query.get_or_404(id)
    
    # 1. Profile Data
    profile_data = [{
        'Field': 'Customer ID', 'Value': c.customer_id},
        {'Field': 'Name', 'Value': c.name},
        {'Field': 'Director', 'Value': c.director.name},
        {'Field': 'Phone', 'Value': c.phone},
        {'Field': 'Father Name', 'Value': c.father_name},
        {'Field': 'Mother Name', 'Value': c.mother_name},
        {'Field': 'DOB', 'Value': c.dob},
        {'Field': 'Religion', 'Value': c.religion},
        {'Field': 'Profession', 'Value': c.profession},
        {'Field': 'NID No', 'Value': c.nid_no},
        {'Field': 'Present Address', 'Value': c.present_address},
        {'Field': 'Permanent Address', 'Value': c.permanent_address},
        {'Field': 'Plot No', 'Value': c.plot_no},
        {'Field': 'Total Price', 'Value': c.total_price},
        {'Field': 'Down Payment', 'Value': c.down_payment},
        {'Field': 'Monthly Installment', 'Value': c.monthly_installment},
        {'Field': 'Total Paid', 'Value': c.total_paid},
        {'Field': 'Due Amount', 'Value': c.due_amount}
    ]
    df_profile = pd.DataFrame(profile_data)
    
    # 2. Transactions Data
    tx_data = []
    for tx in c.transactions:
        tx_data.append({
            'Date': tx.date,
            'Amount': tx.amount,
            'Installment Type': tx.installment_type,
            'Bank Name': tx.bank_name,
            'Transaction ID': tx.transaction_id,
            'Remarks': tx.remarks
        })
    df_tx = pd.DataFrame(tx_data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_profile.to_excel(writer, sheet_name='Profile', index=False)
        format_excel_width(writer, 'Profile')
        
        df_tx.to_excel(writer, sheet_name='Transactions', index=False)
        format_excel_width(writer, 'Transactions')
        
    output.seek(0)
    filename = f"Customer_{secure_filename(c.name)}_Report.xlsx"
    return send_file(output, download_name=filename, as_attachment=True)

    return send_file(output, download_name=filename, as_attachment=True)

# --- Bank Management Routes ---
@main.route('/banks', methods=['GET', 'POST'])
def manage_banks():
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.manage_banks'))

        # Add New Bank
        new_bank = Bank(
            bank_name=request.form.get('bank_name'),
            branch=request.form.get('branch'),
            account_holder_name=request.form.get('account_holder_name'),
            joint_name=request.form.get('joint_name'),
            fhp=request.form.get('fhp'),
            address=request.form.get('address'),
            city=request.form.get('city'),
            phone=request.form.get('phone'),
            customer_id=request.form.get('customer_id'),
            account_no=request.form.get('account_no'),
            prev_account_no=request.form.get('prev_account_no'),
            account_type=request.form.get('account_type'),
            currency=request.form.get('currency'),
            status=request.form.get('status')
        )
        db.session.add(new_bank)
        db.session.commit()
        run_background(backup_to_telegram, "Added Bank: " + request.form.get('bank_name'))
        flash('Bank Account Added!', 'success')
        return redirect(url_for('main.manage_banks'))
        
    banks = Bank.query.all()
    return render_template('banks.html', banks=banks)

@main.route('/bank/edit/<int:id>', methods=['POST'])
def edit_bank(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.manage_banks'))

    bank = Bank.query.get_or_404(id)
    
    bank.bank_name = request.form.get('bank_name')
    bank.branch = request.form.get('branch')
    bank.account_holder_name = request.form.get('account_holder_name')
    bank.joint_name = request.form.get('joint_name')
    bank.fhp = request.form.get('fhp')
    bank.address = request.form.get('address')
    bank.city = request.form.get('city')
    bank.phone = request.form.get('phone')
    bank.customer_id = request.form.get('customer_id')
    bank.account_no = request.form.get('account_no')
    bank.prev_account_no = request.form.get('prev_account_no')
    bank.account_type = request.form.get('account_type')
    bank.currency = request.form.get('currency')
    bank.status = request.form.get('status')
    
    db.session.commit()
    run_background(backup_to_telegram, "Edited Bank: " + bank.bank_name)
    flash('Bank Account Updated!', 'success')
    return redirect(url_for('main.manage_banks'))

@main.route('/bank/delete/<int:id>', methods=['POST'])
def delete_bank(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.manage_banks'))

    bank = Bank.query.get_or_404(id)
    db.session.delete(bank)
    db.session.commit()
    run_background(backup_to_telegram, "Deleted Bank: " + bank.bank_name)
    flash('Bank Account Deleted!', 'warning')
    return redirect(url_for('main.manage_banks'))

@main.route('/bank/<int:id>/ledger', methods=['GET', 'POST'])
def bank_ledger(id):
    bank = Bank.query.get_or_404(id)
    
    # Force recompute on view to ensure existing data is corrected
    recompute_bank_balances(id)
    
    if request.method == 'POST':
        if not verify_password():
            flash('Invalid Admin Password!', 'danger')
            return redirect(url_for('main.bank_ledger', id=id))

        date = request.form.get('date')
        cheque_no = request.form.get('cheque_no')
        ref_no = request.form.get('ref_no')
        narration = request.form.get('narration')
        transaction_details = request.form.get('transaction_details')
        tx_type = request.form.get('tx_type')
        if tx_type == 'credit':
            credit = float(request.form.get('credit') or 0)
            debit = 0.0
        else:
            debit = float(request.form.get('debit') or 0)
            credit = 0.0
        
        new_tx = BankTransaction(
            date=date,
            cheque_no=cheque_no,
            ref_no=ref_no,
            narration=narration,
            transaction_details=transaction_details,
            debit=debit,
            credit=credit,
            balance=0,
            bank=bank
        )

        db.session.add(new_tx)
        db.session.commit()
        
        # Recompute Balances
        recompute_bank_balances(id)
        run_background(backup_to_telegram, "Added Bank Ledger Tx")
        flash('Transaction Added to Ledger!', 'success')
        return redirect(url_for('main.bank_ledger', id=id))
        
    transactions = BankTransaction.query.filter_by(bank_id=id).all()
    
    # Sort in Python to handle Date Parsing correctly
    # DB Date is String, format mostly YYYY-MM-DD or DD-MM-YYYY
    def parse_tx_date(tx):
        for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(tx.date, fmt)
            except ValueError:
                pass
        return datetime.min # Fallback

    transactions.sort(key=lambda x: (parse_tx_date(x), x.id))
    
    # --- Date Filter Logic ---
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str or end_date_str:
        filtered_transactions = []
        
        # Convert filter inputs (YYYY-MM-DD -> datetime)
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else None
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str else None
        
        for tx in transactions:
            try:
                # Use helper or direct parse
                tx_date = parse_tx_date(tx)
                
                # Apply Filter
                if start_date and tx_date < start_date:
                    continue
                if end_date and tx_date > end_date:
                    continue
                
                filtered_transactions.append(tx)
            except ValueError:
                filtered_transactions.append(tx)
                
        transactions = filtered_transactions

    return render_template('bank_ledger.html', bank=bank, transactions=transactions, start_date=start_date_str, end_date=end_date_str)

def recompute_bank_balances(bank_id):
    """
    Recalculates the running balance for all transactions of a specific bank.
    Sorts by Date (asc) and then ID (asc).
    Handles mixed date formats (YYYY-MM-DD vs DD-MM-YYYY).
    """
    transactions = BankTransaction.query.filter_by(bank_id=bank_id).all()
    
    def parse_date(date_str):
        for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                pass
        return datetime.min

    # Sort transactions
    transactions.sort(key=lambda x: (parse_date(x.date), x.id))
    
    running_balance = 0.0
    for tx in transactions:
        running_balance += (tx.credit - tx.debit)
        tx.balance = running_balance
        
    db.session.commit()

@main.route('/bank/transaction/delete/<int:id>', methods=['POST'])
def delete_bank_transaction(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        # We need bank_id to redirect
        tx = BankTransaction.query.get(id)
        if tx:
            return redirect(url_for('main.bank_ledger', id=tx.bank_id))
        return redirect(url_for('main.manage_banks'))

    tx = BankTransaction.query.get_or_404(id)
    bank_id = tx.bank_id
    
    db.session.delete(tx)
    db.session.commit()
    
    recompute_bank_balances(bank_id)
    run_background(backup_to_telegram, "Deleted Bank Tx: " + (tx.narration or str(id)))
    flash('Transaction Deleted & Balances Recomputed!', 'warning')
    return redirect(url_for('main.bank_ledger', id=bank_id))

@main.route('/bank/transaction/edit/<int:id>', methods=['POST'])
def edit_bank_transaction(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        tx = BankTransaction.query.get(id)
        if tx:
            return redirect(url_for('main.bank_ledger', id=tx.bank_id))
        return redirect(url_for('main.manage_banks'))

    tx = BankTransaction.query.get_or_404(id)
    bank_id = tx.bank_id
    
    # Update Data
    tx.date = request.form.get('date')
    tx.cheque_no = request.form.get('cheque_no')
    tx.ref_no = request.form.get('ref_no')
    tx.narration = request.form.get('narration')
    tx.transaction_details = request.form.get('transaction_details')
    tx_type = request.form.get('tx_type')
    if tx_type == 'credit':
        tx.credit = float(request.form.get('credit') or 0)
        tx.debit = 0.0
    else:
        tx.debit = float(request.form.get('debit') or 0)
        tx.credit = 0.0
    
    # Save first to establish new values
    db.session.commit()
    
    recompute_bank_balances(bank_id)
    run_background(backup_to_telegram, "Edited Bank Tx: " + (tx.narration or str(id)))
    flash('Transaction Updated & Balances Recomputed!', 'success')
    return redirect(url_for('main.bank_ledger', id=bank_id))

@main.route('/bank/<int:id>/export')
def export_bank_ledger(id):
    bank = Bank.query.get_or_404(id)
    transactions = BankTransaction.query.filter_by(bank_id=id).order_by(BankTransaction.date).all()
    
    data = []
    for tx in transactions:
        data.append({
            'Date': tx.date,
            'Cheque No': tx.cheque_no,
            'Ref No': tx.ref_no,
            'Narration': tx.narration,
            'Debit': tx.debit,
            'Credit': tx.credit,
            'Balance': tx.balance
        })
        
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        sheet_name = 'Ledger'
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        format_excel_width(writer, sheet_name)
        
    output.seek(0)
    filename = f"Bank_Ledger_{secure_filename(bank.bank_name)}.xlsx"
    return send_file(output, download_name=filename, as_attachment=True)

# --- Settings & Restore ---
@main.route('/settings')
def settings():
    # List available backups
    backups = []
    backup_dir = os.path.join(current_app.root_path, 'backups')
    if os.path.exists(backup_dir):
        files = sorted(os.listdir(backup_dir), reverse=True)
        for f in files:
            if f.endswith('.xlsx'):
                backups.append(f)
    return render_template('settings.html', backups=backups)

@main.route('/settings/restore', methods=['POST'])
def restore_data():
    if 'backup_file' in request.files:
        file = request.files['backup_file']
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            success, msg = restore_from_excel(filepath)
            os.remove(filepath) # Clean up temp file
            
            if success:
                flash('System successfully restored from upload!', 'success')
            else:
                flash(f'Restore failed: {msg}', 'danger')
                
    elif 'backup_filename' in request.form:
        # Restore from internal backup
        filename = request.form.get('backup_filename')
        filepath = os.path.join(current_app.root_path, 'backups', filename)
        if os.path.exists(filepath):
            success, msg = restore_from_excel(filepath)
            if success:
                flash(f'System restored from {filename}', 'success')
            else:
                flash(f'Restore failed: {msg}', 'danger')
        else:
            flash('Backup file not found.', 'danger')
            
    return redirect(url_for('main.settings'))


# ============================================================
# --- Helper: Recalculate Director Totals ---
# ============================================================
def recalculate_director_totals(director_id):
    """
    Recomputes director.total_paid as the sum of all InstallmentPaymentEntry amounts
    for all customers under this director.
    Also updates each customer's total_paid and due_amount from installment payments.
    """
    director = Director.query.get(director_id)
    if not director:
        return

    # Get sum of all installments to calculate dynamic price
    all_installments = Installment.query.all()
    total_inst_val = sum(i.per_share_amount for i in all_installments)

    director_total_paid = 0.0
    for customer in director.customers:
        # Sum all InstallmentPaymentEntry amounts for this customer
        cust_installment_paid = 0.0
        for ip in customer.installment_payments:
            for entry in ip.entries:
                cust_installment_paid += entry.paid_amount
        # Also include legacy Transaction payments
        cust_tx_paid = sum(tx.amount for tx in customer.transactions)
        
        customer.total_paid = cust_installment_paid + cust_tx_paid
        customer.total_price = customer.num_shares * total_inst_val
        # Dynamic Due = (Share Count * Sum of Installments) - Paid
        customer.due_amount = customer.total_price - customer.total_paid
        director_total_paid += customer.total_paid

    director.total_paid = director_total_paid
    db.session.commit()


# ============================================================
# --- Installment Management Routes ---
# ============================================================

@main.route('/installments')
def list_installments():
    installments = Installment.query.order_by(Installment.id.desc()).all()
    customers = Customer.query.all()
    # Compute totals for each installment
    for inst in installments:
        inst._total_collected = sum(
            sum(e.paid_amount for e in ip.entries)
            for ip in inst.payments
        )
        inst._total_due = sum(ip.total_due for ip in inst.payments)
        inst._total_remaining = inst._total_due - inst._total_collected
    return render_template('installments.html', installments=installments, customers=customers)


@main.route('/installments/create', methods=['POST'])
def create_installment():
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_installments'))

    name = request.form.get('name', '').strip()
    per_share_amount = float(request.form.get('per_share_amount') or 0)
    created_date = request.form.get('created_date') or datetime.now().strftime('%Y-%m-%d')

    if not name:
        flash('Installment name is required.', 'warning')
        return redirect(url_for('main.list_installments'))

    # Get all customers
    customers = Customer.query.all()
    total_amount = sum(c.num_shares * per_share_amount for c in customers)

    new_installment = Installment(
        name=name,
        per_share_amount=per_share_amount,
        total_amount=total_amount,
        created_date=created_date
    )
    db.session.add(new_installment)
    db.session.flush()  # Get ID

    # Auto-create InstallmentPayment row for each customer
    for customer in customers:
        customer_due = customer.num_shares * per_share_amount
        ip = InstallmentPayment(
            installment_id=new_installment.id,
            customer_id=customer.id,
            total_due=customer_due,
            total_paid_for_installment=0.0,
            remaining_due=customer_due
        )
        db.session.add(ip)

    db.session.commit()
    run_background(backup_to_telegram, "Created Installment: " + name)
    flash(f'Installment "{name}" created for {len(customers)} customers!', 'success')
    return redirect(url_for('main.list_installments'))


@main.route('/installments/<int:inst_id>')
def installment_detail(inst_id):
    installment = Installment.query.get_or_404(inst_id)
    # Build per-customer payment summary
    payments = InstallmentPayment.query.filter_by(installment_id=inst_id).all()
    banks = Bank.query.all()
    return render_template('installment_detail.html', installment=installment, payments=payments, banks=banks)


@main.route('/installments/<int:inst_id>/edit', methods=['POST'])
def edit_installment(inst_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.installment_detail', inst_id=inst_id))

    installment = Installment.query.get_or_404(inst_id)
    new_name = request.form.get('name', '').strip()
    new_per_share = float(request.form.get('per_share_amount') or 0)

    if not new_name:
        flash('Name is required.', 'warning')
        return redirect(url_for('main.installment_detail', inst_id=inst_id))

    installment.name = new_name
    installment.per_share_amount = new_per_share

    # Recalculate total_due for each customer payment row
    for ip in installment.payments:
        customer = Customer.query.get(ip.customer_id)
        if customer:
            ip.total_due = customer.num_shares * new_per_share
            ip.remaining_due = ip.total_due - ip.total_paid_for_installment

    installment.total_amount = sum(ip.total_due for ip in installment.payments)
    db.session.commit()
    flash(f'Installment updated!', 'success')
    return redirect(url_for('main.installment_detail', inst_id=inst_id))


@main.route('/installments/<int:inst_id>/delete', methods=['POST'])
def delete_installment(inst_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_installments'))

    installment = Installment.query.get_or_404(inst_id)
    inst_name = installment.name

    # Cascade handled by model relationship; also update director totals after
    affected_director_ids = set()
    for ip in installment.payments:
        c = Customer.query.get(ip.customer_id)
        if c:
            affected_director_ids.add(c.director_id)

    db.session.delete(installment)
    db.session.commit()

    # Recalculate director totals
    for did in affected_director_ids:
        recalculate_director_totals(did)
    run_background(backup_to_telegram, "Deleted Installment: " + inst_name)
    flash(f'Installment "{inst_name}" deleted!', 'warning')
    return redirect(url_for('main.list_installments'))


# ============================================================
# --- Installment Payment Routes ---
# ============================================================

@main.route('/installments/<int:inst_id>/customer/<int:cust_id>/pay', methods=['POST'])
def add_installment_payment(inst_id, cust_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.installment_detail', inst_id=inst_id))

    ip = InstallmentPayment.query.filter_by(installment_id=inst_id, customer_id=cust_id).first_or_404()
    installment = ip.installment
    customer = ip.customer

    date = request.form.get('date')
    paid_amount = float(request.form.get('paid_amount') or 0)
    payment_source = request.form.get('payment_source') # 'Cash' or 'Bank'
    bank_id = request.form.get('bank_id')
    transaction_id = request.form.get('transaction_id', '')
    remarks = request.form.get('remarks', '')

    # Handle Images
    images = []
    if 'evidence' in request.files:
        files = request.files.getlist('evidence')
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                images.append(filename)

    image_str = ','.join(images)

    # Create Payment Entry
    entry = InstallmentPaymentEntry(
        installment_payment_id=ip.id,
        date=date,
        paid_amount=paid_amount,
        remaining_due=max(0.0, ip.remaining_due - paid_amount),
        bank_name=payment_source if payment_source == 'Cash' else f"Bank ID: {bank_id}",
        transaction_id=transaction_id,
        remarks=remarks,
        images=image_str
    )
    db.session.add(entry)

    # Update InstallmentPayment running totals
    ip.total_paid_for_installment += paid_amount
    ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)

    # Integrated Ledger Updates
    description = f"Installment: {installment.name} - {customer.name}"
    if payment_source == 'Cash':
        new_petty = PettyCash(
            date=date,
            description=description,
            category="Installment",
            type="Income",
            amount=paid_amount,
            images=image_str
        )
        db.session.add(new_petty)
    elif payment_source == 'Bank' and bank_id:
        bank = Bank.query.get(bank_id)
        if bank:
            new_bank_tx = BankTransaction(
                bank_id=bank.id,
                date=date,
                cheque_no=transaction_id,
                narration=description,
                transaction_details=f"Installment Payment from {customer.name}",
                credit=paid_amount,
                balance=0.0 # Will be recomputed
            )
            db.session.add(new_bank_tx)
            db.session.flush() # Get ID for recompute
            # Recompute bank balance
            recompute_bank_balances(bank.id)

    db.session.commit()

    # Recalculate director totals
    recalculate_director_totals(customer.director_id)
    run_background(backup_to_telegram, f"Added Installment Payment for {customer.name}")
    flash('Payment recorded and ledger updated!', 'success')
    return redirect(url_for('main.installment_detail', inst_id=inst_id))


@main.route('/installment_payment_entry/<int:entry_id>/delete', methods=['POST'])
def delete_installment_payment_entry(entry_id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.list_installments'))

    entry = InstallmentPaymentEntry.query.get_or_404(entry_id)
    ip = entry.installment_payment
    inst_id = ip.installment_id
    cust_id = ip.customer_id

    # Revert amounts
    ip.total_paid_for_installment -= entry.paid_amount
    ip.remaining_due = max(0.0, ip.total_due - ip.total_paid_for_installment)

    db.session.delete(entry)
    db.session.commit()

    # Recalculate director totals
    customer = Customer.query.get(cust_id)
    if customer:
        recalculate_director_totals(customer.director_id)
    flash('Payment entry deleted!', 'warning')
    return redirect(url_for('main.installment_detail', inst_id=inst_id))

# ============================================================
# --- Petty Cash Category Management ---
# ============================================================

@main.route('/petty_cash/categories')
def manage_petty_cash_categories():
    categories = PettyCashCategory.query.order_by(PettyCashCategory.name).all()
    return render_template('petty_cash_categories.html', categories=categories)

@main.route('/petty_cash/categories/add', methods=['POST'])
def add_petty_cash_category():
    name = request.form.get('name', '').strip()
    if name:
        if PettyCashCategory.query.filter_by(name=name).first():
            flash('Category already exists.', 'warning')
        else:
            new_cat = PettyCashCategory(name=name)
            db.session.add(new_cat)
            db.session.commit()
            flash('Category added.', 'success')
    return redirect(url_for('main.manage_petty_cash_categories'))

@main.route('/petty_cash/categories/edit/<int:id>', methods=['POST'])
def edit_petty_cash_category(id):
    cat = PettyCashCategory.query.get_or_404(id)
    new_name = request.form.get('name', '').strip()
    if new_name:
        # Check if another category has the same name
        existing = PettyCashCategory.query.filter(
            PettyCashCategory.name == new_name, 
            PettyCashCategory.id != id
        ).first()
        if existing:
            flash('Category name already exists.', 'warning')
        else:
            old_name = cat.name
            cat.name = new_name
            # Update all transactions that use this category name
            PettyCash.query.filter_by(category=old_name).update({PettyCash.category: new_name})
            db.session.commit()
            flash('Category updated and transactions linked.', 'success')
    return redirect(url_for('main.manage_petty_cash_categories'))

@main.route('/petty_cash/categories/delete/<int:id>', methods=['POST'])
def delete_petty_cash_category(id):
    if not verify_password():
        flash('Invalid Admin Password!', 'danger')
        return redirect(url_for('main.manage_petty_cash_categories'))
    
    cat = PettyCashCategory.query.get_or_404(id)
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted.', 'warning')
    return redirect(url_for('main.manage_petty_cash_categories'))


# ============================================================
# --- Reports Hub ---
# ============================================================

@main.route('/reports')
def reports_hub():
    """Main reports page with daily, monthly, and installment tabs."""
    # Default to today for daily report
    today = datetime.now().strftime('%Y-%m-%d')
    current_month = datetime.now().strftime('%Y-%m')

    selected_date = request.args.get('date', today)
    selected_month = request.args.get('month', current_month)

    # ---- Daily Cash Report ----
    daily_cash_income = db.session.query(func.sum(PettyCash.amount)).filter(
        PettyCash.type == 'Income', PettyCash.date == selected_date
    ).scalar() or 0
    daily_cash_expense = db.session.query(func.sum(PettyCash.amount)).filter(
        PettyCash.type == 'Expense', PettyCash.date == selected_date
    ).scalar() or 0
    daily_bank_credit = db.session.query(func.sum(BankTransaction.credit)).filter(
        BankTransaction.date == selected_date
    ).scalar() or 0
    daily_bank_debit = db.session.query(func.sum(BankTransaction.debit)).filter(
        BankTransaction.date == selected_date
    ).scalar() or 0

    daily_cash_entries = PettyCash.query.filter(PettyCash.date == selected_date).order_by(PettyCash.id.desc()).all()
    daily_bank_entries = BankTransaction.query.filter(BankTransaction.date == selected_date).order_by(BankTransaction.id.desc()).all()

    # ---- Monthly Summary ----
    # Last 6 months
    monthly_data = []
    today_dt = datetime.now()
    for i in range(5, -1, -1):
        month = today_dt.month - i
        year = today_dt.year
        while month <= 0:
            month += 12
            year -= 1
        m_str = f"{year}-{month:02d}"
        month_label = datetime(year, month, 1).strftime('%b %Y')

        m_cash_in = db.session.query(func.sum(PettyCash.amount)).filter(
            PettyCash.type == 'Income', PettyCash.date.like(f'{m_str}%')
        ).scalar() or 0
        m_cash_out = db.session.query(func.sum(PettyCash.amount)).filter(
            PettyCash.type == 'Expense', PettyCash.date.like(f'{m_str}%')
        ).scalar() or 0
        m_bank_in = db.session.query(func.sum(BankTransaction.credit)).filter(
            BankTransaction.date.like(f'{m_str}%')
        ).scalar() or 0
        m_bank_out = db.session.query(func.sum(BankTransaction.debit)).filter(
            BankTransaction.date.like(f'{m_str}%')
        ).scalar() or 0

        # Installment collected this month
        m_inst = 0
        inst_entries = InstallmentPaymentEntry.query.filter(
            InstallmentPaymentEntry.date.like(f'{m_str}%')
        ).all()
        m_inst = sum(e.paid_amount for e in inst_entries)

        monthly_data.append({
            'month': month_label,
            'month_str': m_str,
            'cash_income': m_cash_in,
            'cash_expense': m_cash_out,
            'bank_credit': m_bank_in,
            'bank_debit': m_bank_out,
            'total_income': m_cash_in + m_bank_in,
            'total_expense': m_cash_out + m_bank_out,
            'net': (m_cash_in + m_bank_in) - (m_cash_out + m_bank_out),
            'installment_collected': m_inst,
        })

    # ---- Installment Status Report ----
    installments = Installment.query.order_by(Installment.id.desc()).all()
    inst_report = []
    for inst in installments:
        total_due = sum(ip.total_due for ip in inst.payments)
        total_collected = sum(
            sum(e.paid_amount for e in ip.entries) for ip in inst.payments
        )
        total_remaining = total_due - total_collected
        pct = round((total_collected / total_due * 100) if total_due > 0 else 0, 1)
        inst_report.append({
            'id': inst.id,
            'name': inst.name,
            'date': inst.created_date,
            'per_share': inst.per_share_amount,
            'total_due': total_due,
            'total_collected': total_collected,
            'total_remaining': total_remaining,
            'pct': pct,
        })

    # ---- Director Collection Report ----
    directors = Director.query.all()
    all_installments_list = Installment.query.all()
    total_per_share = sum(i.per_share_amount for i in all_installments_list)
    director_report = []
    for d in directors:
        d_total_due = (d.total_share or 0) * total_per_share
        d_total_paid = sum(c.total_paid or 0 for c in d.customers)
        d_remaining = d_total_due - d_total_paid
        d_pct = round((d_total_paid / d_total_due * 100) if d_total_due > 0 else 0, 1)
        director_report.append({
            'name': d.name,
            'shares': d.total_share,
            'total_due': d_total_due,
            'total_paid': d_total_paid,
            'remaining': d_remaining,
            'pct': d_pct,
            'num_customers': len(d.customers),
        })

    # Overall totals
    grand_due = sum(r['total_due'] for r in director_report)
    grand_paid = sum(r['total_paid'] for r in director_report)
    grand_remaining = grand_due - grand_paid
    grand_pct = round((grand_paid / grand_due * 100) if grand_due > 0 else 0, 1)

    return render_template('reports.html',
        selected_date=selected_date,
        selected_month=selected_month,
        # Daily
        daily_cash_income=daily_cash_income,
        daily_cash_expense=daily_cash_expense,
        daily_bank_credit=daily_bank_credit,
        daily_bank_debit=daily_bank_debit,
        daily_net_cash=daily_cash_income - daily_cash_expense,
        daily_net_bank=daily_bank_credit - daily_bank_debit,
        daily_grand_net=(daily_cash_income + daily_bank_credit) - (daily_cash_expense + daily_bank_debit),
        daily_cash_entries=daily_cash_entries,
        daily_bank_entries=daily_bank_entries,
        # Monthly
        monthly_data=monthly_data,
        # Installment
        inst_report=inst_report,
        # Director
        director_report=director_report,
        grand_due=grand_due,
        grand_paid=grand_paid,
        grand_remaining=grand_remaining,
        grand_pct=grand_pct,
        now=datetime.now()
    )


@main.route('/reports/daily/print')
def print_daily_report():
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    daily_cash_income = db.session.query(func.sum(PettyCash.amount)).filter(
        PettyCash.type == 'Income', PettyCash.date == selected_date
    ).scalar() or 0
    daily_cash_expense = db.session.query(func.sum(PettyCash.amount)).filter(
        PettyCash.type == 'Expense', PettyCash.date == selected_date
    ).scalar() or 0
    daily_bank_credit = db.session.query(func.sum(BankTransaction.credit)).filter(
        BankTransaction.date == selected_date
    ).scalar() or 0
    daily_bank_debit = db.session.query(func.sum(BankTransaction.debit)).filter(
        BankTransaction.date == selected_date
    ).scalar() or 0

    daily_cash_entries = PettyCash.query.filter(PettyCash.date == selected_date).order_by(PettyCash.id.desc()).all()
    daily_bank_entries = BankTransaction.query.filter(BankTransaction.date == selected_date).order_by(BankTransaction.id.desc()).all()

    return render_template('print_daily_report.html',
        selected_date=selected_date,
        daily_cash_income=daily_cash_income,
        daily_cash_expense=daily_cash_expense,
        daily_bank_credit=daily_bank_credit,
        daily_bank_debit=daily_bank_debit,
        daily_net_cash=daily_cash_income - daily_cash_expense,
        daily_net_bank=daily_bank_credit - daily_bank_debit,
        daily_grand_net=(daily_cash_income + daily_bank_credit) - (daily_cash_expense + daily_bank_debit),
        daily_cash_entries=daily_cash_entries,
        daily_bank_entries=daily_bank_entries,
        now=datetime.now()
    )


@main.route('/reports/daily/export')
def export_daily_report():
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))

    cash_entries = PettyCash.query.filter(PettyCash.date == selected_date).all()
    bank_entries = BankTransaction.query.filter(BankTransaction.date == selected_date).all()

    cash_data = [{
        'Date': e.date, 'Description': e.description, 'Category': e.category,
        'Type': e.type,
        'Income': e.amount if e.type == 'Income' else 0,
        'Expense': e.amount if e.type == 'Expense' else 0,
    } for e in cash_entries]

    bank_data = [{
        'Date': tx.date, 'Narration': tx.narration,
        'Transaction Details': tx.transaction_details,
        'Cheque No': tx.cheque_no, 'Ref No': tx.ref_no,
        'Credit': tx.credit, 'Debit': tx.debit, 'Balance': tx.balance,
    } for tx in bank_entries]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(cash_data).to_excel(writer, sheet_name='Cash_Transactions', index=False)
        pd.DataFrame(bank_data).to_excel(writer, sheet_name='Bank_Transactions', index=False)
        format_excel_width(writer, 'Cash_Transactions')
        format_excel_width(writer, 'Bank_Transactions')
    output.seek(0)
    return send_file(output, download_name=f"Daily_Report_{selected_date}.xlsx", as_attachment=True)


@main.route('/reports/monthly/export')
def export_monthly_report():
    selected_month = request.args.get('month', datetime.now().strftime('%Y-%m'))

    cash_entries = PettyCash.query.filter(PettyCash.date.like(f'{selected_month}%')).all()
    bank_entries = BankTransaction.query.filter(BankTransaction.date.like(f'{selected_month}%')).all()
    inst_entries = InstallmentPaymentEntry.query.filter(InstallmentPaymentEntry.date.like(f'{selected_month}%')).all()

    cash_data = [{'Date': e.date, 'Description': e.description, 'Category': e.category, 'Type': e.type,
                  'Income': e.amount if e.type == 'Income' else 0,
                  'Expense': e.amount if e.type == 'Expense' else 0} for e in cash_entries]
    bank_data = [{'Date': tx.date, 'Narration': tx.narration, 'Credit': tx.credit, 'Debit': tx.debit, 'Balance': tx.balance} for tx in bank_entries]
    inst_data = [{'Date': e.date, 'Customer': e.installment_payment.customer.name if e.installment_payment else '',
                  'Installment': e.installment_payment.installment.name if e.installment_payment else '',
                  'Amount': e.paid_amount} for e in inst_entries]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(cash_data).to_excel(writer, sheet_name='Cash', index=False)
        pd.DataFrame(bank_data).to_excel(writer, sheet_name='Bank', index=False)
        pd.DataFrame(inst_data).to_excel(writer, sheet_name='Installments', index=False)
        format_excel_width(writer, 'Cash')
        format_excel_width(writer, 'Bank')
        format_excel_width(writer, 'Installments')
    output.seek(0)
    return send_file(output, download_name=f"Monthly_Report_{selected_month}.xlsx", as_attachment=True)


@main.route('/reports/installments/export')
def export_installment_report():
    installments = Installment.query.order_by(Installment.id.desc()).all()
    data = []
    for inst in installments:
        total_due = sum(ip.total_due for ip in inst.payments)
        total_collected = sum(sum(e.paid_amount for e in ip.entries) for ip in inst.payments)
        data.append({
            'Installment': inst.name,
            'Date': inst.created_date,
            'Per Share Amount': inst.per_share_amount,
            'Total Due': total_due,
            'Total Collected': total_collected,
            'Remaining': total_due - total_collected,
            'Progress (%)': round((total_collected / total_due * 100) if total_due > 0 else 0, 1)
        })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(data).to_excel(writer, sheet_name='Installment_Report', index=False)
        format_excel_width(writer, 'Installment_Report')
    output.seek(0)
    return send_file(output, download_name='Installment_Collection_Report.xlsx', as_attachment=True)

