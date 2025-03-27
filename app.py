from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import InputRequired, Email, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash
import os

import requests


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SECRET_KEY'] = 'supersecretkey'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    comp_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class RegisterForm(FlaskForm):
    role = SelectField('Выберите роль', choices=[('company', 'Компания'), ('employee', 'Сотрудник')])
    name = StringField('ФИО (если сотрудник) / Название компании', validators=[InputRequired()])
    email = StringField('Email', validators=[InputRequired()])
    comp_name = StringField('Название компании (для сотрудников)')
    password = PasswordField('Пароль', validators=[InputRequired(), EqualTo('confirm', message='Пароли должны совпадать')])
    confirm = PasswordField('Повтор пароля')
    submit = SubmitField('Регистрация')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[InputRequired()])
    password = PasswordField('Пароль', validators=[InputRequired()])
    submit = SubmitField('Войти')


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
        user = User(role=form.role.data, name=form.name.data, comp_name=form.comp_name.data, email=form.email.data, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('login.html', form=form)

# Панель пользователя (компания или сотрудник)
@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'company':
        return render_template('company_dashboard.html', current_user=current_user)
    else:
        return render_template('employee_dashboard.html', current_user=current_user)


# Выход
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# Данные сотрудников (для компании)
@app.route('/employees')
@login_required
def view_employees():
    if current_user.role != 'company':
        return redirect(url_for('dashboard'))

    employees = User.query.filter_by(comp_name=current_user.name, role='employee').all()
    return render_template('employees.html', employees=employees)


# Создание тестирования (генерация ссылки на Google Forms)
@app.route('/create_test')
@login_required
def create_test():
    if current_user.role != 'company':
        return redirect(url_for('dashboard'))

    test_link = f"https://docs.google.com/forms/d/e/1FAIpQLSf6kg9a-MGo7PLcUhayA0C5VWGOuM8323N8-CJt0AsmsExLLg/viewform"
    flash(f'Ссылка на тест: {test_link}')
    return redirect(url_for('dashboard'))


# Пройти тестирование (для сотрудников)
@app.route('/take_test')
@login_required
def take_test():
    if current_user.role != 'employee':
        return redirect(url_for('dashboard'))

    company = User.query.filter_by(name=current_user.comp_name, role='company').first()
    if not company:
        flash('Компания не настроила тестирование')
        return redirect(url_for('dashboard'))

    test_link = f"https://docs.google.com/forms/d/e/1FAIpQLSf6kg9a-MGo7PLcUhayA0C5VWGOuM8323N8-CJt0AsmsExLLg/viewform"
    return redirect(test_link)

from flask import send_file
import io

@app.route('/download_results')
@login_required
def download_results():
    if current_user.role != 'company':
        return redirect(url_for('dashboard'))

    # Прямая ссылка на CSV-файл (нужно использовать Google Sheets, а не Google Forms!)
    csv_url = "https://docs.google.com/spreadsheets/d/1PPz2p6kqqV90PbTso4WtxAlinkLp44tZyLdqOfxZmKg/gviz/tq?tqx=out:csv"

    # Загружаем CSV
    response = requests.get(csv_url)

    if response.status_code == 200:
        # Отправляем CSV-файл пользователю без сохранения на сервере
        return send_file(
            io.BytesIO(response.content),
            mimetype="text/csv",
            as_attachment=True,
            download_name="test_results.csv"
        )
    else:
        flash('Не удалось загрузить результаты')
        return redirect(url_for('dashboard'))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        app.run(debug=True)
