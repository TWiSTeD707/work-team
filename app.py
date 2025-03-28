from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, DateField
from wtforms.validators import InputRequired, EqualTo, Length, ValidationError, DataRequired
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import uuid
import os
import json
from threading import Thread
import ollama
from pathlib import Path
from sqlalchemy import desc
from datetime import timedelta

app = Flask(__name__)
app.config.from_pyfile('config.py')
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

DEEPSEEK_MODEL = "deepseek-r1:7b"
SYSTEM_PROMPT = """
Ты — HR-аналитик с экспертизой в психологии команд. Анализируй данные по критериям:
1. Распределение ролей по Белбину
2. Психологическая совместимость
3. Потенциальные конфликты
4. Рекомендации по оптимизации

Формат ответа (JSON):
{
  "disc_analysis": {
    "d": {"count": int, "description": str},
    "i": {"count": int, "description": str},
    "s": {"count": int, "description": str},
    "c": {"count": int, "description": str}
  },
  "eq_analysis": {
    "average_score": float,
    "strong_areas": [str],
    "weak_areas": [str]
  },
  "compatibility": {
    "score": int,
    "conflict_warnings": [str],
    "synergy_pairs": [str]
  },
  "recommendations": {
    "individual": [{"name": str, "advice": str}],
    "team": [str]
  }
}
"""


class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    comp_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    test_link = db.Column(db.String(500), nullable=True)
    test_expires = db.Column(db.DateTime, nullable=True)


class Test(db.Model):
    __tablename__ = 'tests'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    end_date = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class Question(db.Model):
    __tablename__ = 'questions'
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(500), nullable=False)
    question_type = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50))


class Answer(db.Model):
    __tablename__ = 'answers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'), nullable=False)
    test_id = db.Column(db.Integer, db.ForeignKey('tests.id'), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class TestQuestion(db.Model):
    __tablename__ = 'test_questions'
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('tests.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'), nullable=False)


class AnalysisResult(db.Model):
    __tablename__ = 'analysis_results'
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('tests.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='processing')
    model = db.Column(db.String(50), nullable=False)
    result_data = db.Column(db.Text)
    report_filename = db.Column(db.String(100))
    error = db.Column(db.Text)


class RegisterForm(FlaskForm):
    role = SelectField('Роль', choices=[('company', 'Компания'), ('employee', 'Сотрудник')],
                       validators=[InputRequired()])
    name = StringField('ФИО/Название компании', validators=[InputRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[InputRequired()])
    comp_name = StringField('Название компании (для сотрудников)')
    password = PasswordField('Пароль', validators=[InputRequired(), Length(min=8), EqualTo('confirm')])
    confirm = PasswordField('Повторите пароль')
    submit = SubmitField('Зарегистрироваться')


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[InputRequired()])
    password = PasswordField('Пароль', validators=[InputRequired()])
    submit = SubmitField('Войти')


class TestCreationForm(FlaskForm):
    end_date = DateField('Дата окончания', format='%Y-%m-%d', validators=[DataRequired()],
                         render_kw={"min": datetime.now().strftime('%Y-%m-%d')})
    submit = SubmitField('Создать тестирование')

    def validate_end_date(form, field):
        if field.data < datetime.now().date():
            raise ValidationError('Дата должна быть в будущем')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def analyze_with_deepseek(test_data):
    try:
        response = ollama.generate(
            model=DEEPSEEK_MODEL,
            system=SYSTEM_PROMPT,
            prompt=json.dumps(test_data),
            format="json",
            options={'temperature': 0.3, 'num_ctx': 4096, 'top_p': 0.9}
        )
        return json.loads(response['response'])
    except Exception as e:
        print(f"Ошибка анализа: {str(e)}")
        return {"error": str(e)}


def generate_team_report(analysis_data):
    return f"""# Отчет по анализу команды
**Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}
**Модель:** {DEEPSEEK_MODEL}

## 1. Распределение психотипов (DISC)
{format_disc_section(analysis_data.get('disc_analysis', {}))}

## 2. Анализ эмоционального интеллекта
{format_eq_section(analysis_data.get('eq_analysis', {}))}

## 3. Совместимость в команде
{format_compatibility_section(analysis_data.get('compatibility', {}))}

## 4. Рекомендации
{format_recommendations_section(analysis_data.get('recommendations', {}))}
"""


def format_disc_section(disc_data):
    sections = []
    for type_key, data in disc_data.items():
        sections.append(
            f"### Тип {type_key.upper()}:\n- Количество: {data.get('count', 0)}\n- Описание: {data.get('description', 'Нет данных')}\n")
    return "\n".join(sections)


def format_eq_section(eq_data):
    return (f"### Средний балл: {eq_data.get('average_score', 0):.1f}/100\n\n" +
            "#### Сильные стороны:\n" + "\n".join(f"- {area}" for area in eq_data.get('strong_areas', [])) +
            "\n\n#### Слабые стороны:\n" + "\n".join(f"- {area}" for area in eq_data.get('weak_areas', [])))


def format_compatibility_section(compat_data):
    return (f"### Оценка совместимости: {compat_data.get('score', 0)}/10\n\n" +
            "#### Потенциальные конфликты:\n" + "\n".join(
                f"- {warning}" for warning in compat_data.get('conflict_warnings', [])) +
            "\n\n#### Синергичные пары:\n" + "\n".join(f"- {pair}" for pair in compat_data.get('synergy_pairs', [])))


def format_recommendations_section(recommendations):
    individual = "\n".join(
        f"- **{item.get('name', '')}**: {item.get('advice', '')}" for item in recommendations.get('individual', []))
    team = "\n".join(f"- {rec}" for rec in recommendations.get('team', []))
    return ("### Индивидуальные рекомендации:\n" + individual + "\n\n### Рекомендации для команды:\n" + team)


def save_report(content, filename):
    report_path = os.path.join('reports', filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return filename


def prepare_test_data(test_id):
    from sqlalchemy.orm import joinedload
    test = db.session.query(Test).filter_by(id=test_id).first()
    if not test:
        raise ValueError("Тест не найден")
    if test.company_id != current_user.id:
        raise PermissionError("Нет доступа к этому тесту")
    employees = db.session.query(User).filter_by(comp_name=current_user.name, role='employee').all()
    test_questions = db.session.query(TestQuestion).filter_by(test_id=test_id).options(
        joinedload(TestQuestion.question)).all()
    test_data = {'disc_results': [], 'eq_results': [], 'team_size': len(employees), 'industry': "IT"}
    for employee in employees:
        answers = db.session.query(Answer).filter_by(user_id=employee.id, test_id=test_id).all()
        if not answers:
            continue
        disc_answers = []
        eq_answers = []
        for answer in answers:
            question = next((tq.question for tq in test_questions if tq.question_id == answer.question_id), None)
            if not question:
                continue
            if question.question_type == 'disc':
                disc_answers.append({'question_type': question.category, 'value': answer.value})
            elif question.question_type == 'eq':
                eq_answers.append({'category': question.category, 'value': answer.value})
        if disc_answers:
            disc_scores = calculate_disc_scores(disc_answers)
            test_data['disc_results'].append(
                {'name': employee.name, 'd': disc_scores.get('d', 0), 'i': disc_scores.get('i', 0),
                 's': disc_scores.get('s', 0), 'c': disc_scores.get('c', 0)})
        if eq_answers:
            eq_score = calculate_eq_score(eq_answers)
            test_data['eq_results'].append({'name': employee.name, 'score': eq_score})
    return test_data


def calculate_disc_scores(answers):
    scores = {'d': 0, 'i': 0, 's': 0, 'c': 0}
    type_mapping = {'d': 'd', 'i': 'i', 's': 's', 'c': 'c', 'dominance': 'd', 'influence': 'i', 'steadiness': 's',
                    'compliance': 'c'}
    for answer in answers:
        question_type = answer['question_type'].lower()
        normalized_type = type_mapping.get(question_type)
        if normalized_type in scores:
            scores[normalized_type] += answer['value']
    total = sum(scores.values()) or 1
    return {k: round((v / total) * 100) for k, v in scores.items()}


def calculate_eq_score(answers):
    if not answers:
        return 0
    return round(sum(answer['value'] for answer in answers) / len(answers), 1)


def create_disc_questions(test_id):
    questions = [
        ("Я легко адаптируюсь к новым ситуациям.", "i"),
        ("Я люблю быть в центре внимания.", "i"),
        ("Я предпочитаю работать в одиночку.", "s"),
        ("Я часто беру на себя ответственность в группе.", "d"),
        ("Я стараюсь избегать конфликтов.", "s"),
        ("Я быстро принимаю решения.", "d"),
        ("Я ценю стабильность и предсказуемость.", "s"),
        ("Я люблю соревноваться и побеждать.", "d"),
        ("Я часто помогаю другим, даже если это не в моих интересах.", "s"),
        ("Я легко нахожу общий язык с новыми людьми.", "i"),
        ("Я предпочитаю тщательно анализировать информацию перед принятием решения.", "c"),
        ("Я люблю рисковать.", "d"),
        ("Я стараюсь избегать резких изменений.", "s"),
        ("Я часто выступаю инициатором новых идей.", "i"),
        ("Я предпочитаю работать в команде, а не в одиночку.", "i"),
        ("Я часто ставлю перед собой амбициозные цели.", "d"),
        ("Я стараюсь избегать конфронтации.", "s"),
        ("Я люблю, когда всё идет по плану.", "c"),
        ("Я часто беру на себя роль лидера.", "d"),
        ("Я ценю гармонию в отношениях.", "s"),
        ("Я быстро устаю от рутины.", "i"),
        ("Я предпочитаю действовать, а не долго обсуждать.", "d"),
        ("Я стараюсь быть дипломатичным в общении.", "s"),
        ("Я люблю решать сложные задачи.", "c"),
        ("Я часто сомневаюсь в своих решениях.", "s"),
        ("Я люблю, когда меня хвалят за мои достижения.", "i"),
        ("Я предпочитаю следовать правилам.", "c"),
        ("Я часто ищу новые возможности для роста.", "d"),
        ("Я стараюсь избегать споров.", "s"),
        ("Я люблю, когда всё организовано и структурировано.", "c")
    ]
    for text, q_type in questions:
        question = Question(text=text, question_type="disc", category=q_type)
        db.session.add(question)
        db.session.flush()
        test_question = TestQuestion(test_id=test_id, question_id=question.id)
        db.session.add(test_question)


def create_eq_questions(test_id):
    questions = [
        (
            "Для меня как отрицательные, так и положительные эмоции служат источником знания о том, как поступать в жизни.",
            "awareness"),
        ("Отрицательные эмоции помогают мне понять, что я должен изменить в своей жизни.", "awareness"),
        ("Я спокоен, когда испытываю давление со стороны.", "management"),
        ("Я способен наблюдать изменение своих чувств.", "awareness"),
        (
            "Когда необходимо, я могу быть спокойным и сосредоточенным, чтобы действовать в соответствии с запросами жизни.",
            "management"),
        (
            "Когда необходимо, я могу вызвать у себя широкий спектр положительных эмоций, такие, как веселье, радость, внутренний подъем и юмор.",
            "management"),
        ("Я слежу за тем, как я себя чувствую.", "awareness"),
        ("После того как что-то расстроило меня, я могу легко совладать со своими чувствами.", "management"),
        ("Я способен выслушивать проблемы других людей.", "empathy"),
        ("Я не зацикливаюсь на отрицательных эмоциях.", "management"),
        ("Я чувствителен к эмоциональным потребностям других.", "empathy"),
        ("Я могу действовать на других людей успокаивающе.", "empathy"),
        ("Я могу заставить себя снова и снова встать перед лицом препятствия.", "motivation"),
        ("Я стараюсь подходить к жизненным проблемам творчески.", "motivation"),
        ("Я адекватно реагирую на настроения, побуждения и желания других людей.", "empathy"),
        ("Я могу легко входить в состояние спокойствия, готовности и сосредоточенности.", "management"),
        ("Когда позволяет время, я обращаюсь к своим негативным чувствам и разбираюсь, в чем проблема.", "awareness"),
        ("Я способен быстро успокоиться после неожиданного огорчения.", "management"),
        ("Знание моих истинных чувств важно для поддержания «хорошей формы».", "awareness"),
        ("Я хорошо понимаю эмоции других людей, даже если они не выражены открыто.", "recognition"),
        ("Я могу хорошо распознавать эмоции по выражению лица.", "recognition"),
        ("Я могу легко отбросить негативные чувства, когда необходимо действовать.", "management"),
        ("Я хорошо улавливаю знаки в общении, которые указывают на то, в чем другие нуждаются.", "recognition"),
        ("Люди считают меня хорошим знатоком переживаний других людей.", "empathy"),
        ("Люди, осознающие свои истинные чувства, лучше управляют своей жизнью.", "awareness"),
        ("Я способен улучшить настроение других людей.", "empathy"),
        ("Со мной можно посоветоваться по вопросам отношений между людьми.", "empathy"),
        ("Я хорошо настраиваюсь на эмоции других людей.", "empathy"),
        ("Я помогаю другим использовать их побуждения для достижения личных целей.", "motivation"),
        ("Я могу легко отключиться от переживания неприятностей.", "management")
    ]
    for text, category in questions:
        question = Question(text=text, question_type="eq", category=category)
        db.session.add(question)
        db.session.flush()
        test_question = TestQuestion(test_id=test_id, question_id=question.id)
        db.session.add(test_question)


@app.route('/company/dashboard')
@login_required
def company_dashboard():
    if current_user.role != 'company':
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    active_tests = Test.query.filter(
        Test.company_id == current_user.id,
        Test.is_active == True,
        Test.end_date > datetime.utcnow()
    ).order_by(Test.end_date).all()

    available_reports = AnalysisResult.query.filter(
        AnalysisResult.user_id == current_user.id,
        AnalysisResult.status == 'completed'
    ).order_by(desc(AnalysisResult.completed_at)).limit(5).all()

    return render_template('company_dashboard.html',
                           active_tests=active_tests,
                           available_reports=available_reports)


@app.route('/employee/dashboard')
@login_required
def employee_dashboard():
    if current_user.role != 'employee':
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    last_test = db.session.query(Test, Answer).join(
        Answer, Answer.test_id == Test.id
    ).filter(
        Answer.user_id == current_user.id,
        Test.is_active == False
    ).order_by(desc(Test.end_date)).first()

    return render_template('employee_dashboard.html', last_test=last_test)


@app.route('/api/employee/results')
@login_required
def get_employee_results():
    if current_user.role != 'employee':
        return jsonify({'error': 'Доступ запрещен'}), 403

    results = db.session.query(
        Test.id,
        Test.end_date,
        db.func.avg(Answer.value).label('avg_score')
    ).join(
        Answer, Answer.test_id == Test.id
    ).filter(
        Answer.user_id == current_user.id
    ).group_by(Test.id).order_by(desc(Test.end_date)).limit(5).all()

    return jsonify([{
        'test_id': r.id,
        'date': r.end_date.strftime('%Y-%m-%d'),
        'score': round(r.avg_score, 1)
    } for r in results])


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            hashed_password = generate_password_hash(form.password.data)
            user = User(role=form.role.data, name=form.name.data,
                        comp_name=form.comp_name.data if form.role.data == 'employee' else None, email=form.email.data,
                        password=hashed_password)
            db.session.add(user)
            db.session.commit()
            flash('Регистрация успешна! Теперь войдите.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {str(e)}', 'danger')
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Неверные данные', 'danger')
    return render_template('login.html', form=form)


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'company':
        return render_template('company_dashboard.html')
    return render_template('employee_dashboard.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))


@app.route('/employees')
@login_required
def view_employees():
    if current_user.role != 'company':
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))
    employees = User.query.filter_by(comp_name=current_user.name, role='employee').all()
    return render_template('employees.html', employees=employees)


@app.route('/create_test', methods=['GET', 'POST'])
@login_required
def create_test():
    if current_user.role != 'company':
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))
    form = TestCreationForm()
    if form.validate_on_submit():
        try:
            end_date = datetime.combine(form.end_date.data, datetime.max.time())
            test = Test(company_id=current_user.id, end_date=end_date, is_active=True)
            db.session.add(test)
            db.session.flush()
            create_disc_questions(test.id)
            create_eq_questions(test.id)
            db.session.commit()
            flash('Тестирование успешно создано!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {str(e)}', 'danger')
    return render_template('create_test.html', form=form)


@app.route('/start_deepseek_analysis', methods=['POST'])
@login_required
def start_deepseek_analysis():
    if current_user.role != 'company':
        return jsonify({"error": "Доступ запрещен"}), 403
    test_id = request.json.get('test_id')
    test_data = prepare_test_data(test_id)
    analysis = AnalysisResult(test_id=test_id, user_id=current_user.id, status='processing', model=DEEPSEEK_MODEL)
    db.session.add(analysis)
    db.session.commit()
    Thread(target=run_deepseek_analysis, args=(test_data, analysis.id)).start()
    return jsonify({"status": "started", "analysis_id": analysis.id})


def run_deepseek_analysis(test_data, analysis_id):
    with app.app_context():
        analysis = AnalysisResult.query.get(analysis_id)
        try:
            result = analyze_with_deepseek(test_data)
            if 'error' in result:
                raise Exception(result['error'])
            report = generate_team_report(result)
            report_filename = f"report_{analysis_id}.md"
            save_report(report, report_filename)
            analysis.status = 'completed'
            analysis.result_data = json.dumps(result, ensure_ascii=False)
            analysis.report_filename = report_filename
            analysis.completed_at = datetime.utcnow()
        except Exception as e:
            analysis.status = 'failed'
            analysis.error = str(e)
        db.session.commit()


@app.route('/api/analysis_status/<int:analysis_id>')
@login_required
def analysis_status(analysis_id):
    analysis = AnalysisResult.query.get_or_404(analysis_id)
    if analysis.user_id != current_user.id:
        return jsonify({"error": "Доступ запрещен"}), 403
    if analysis.status == 'completed':
        return jsonify({"completed": True, "progress": 100, "message": "Анализ завершен",
                        "result": json.loads(analysis.result_data)})
    elif analysis.status == 'failed':
        return jsonify({"error": analysis.error or "Ошибка анализа"}), 500
    progress = min(90, int((datetime.utcnow() - analysis.created_at).total_seconds() / 60 * 10))
    return jsonify({"completed": False, "progress": progress, "message": "Идет анализ данных..."})


@app.route('/download_report/<int:analysis_id>')
@login_required
def download_report(analysis_id):
    analysis = AnalysisResult.query.get_or_404(analysis_id)
    if analysis.user_id != current_user.id:
        flash("Доступ запрещен", "danger")
        return redirect(url_for('dashboard'))
    if analysis.status != 'completed':
        flash("Отчет еще не готов", "warning")
        return redirect(url_for('dashboard'))
    report_path = os.path.join('reports', analysis.report_filename)
    if not os.path.exists(report_path):
        flash("Файл отчета не найден", "danger")
        return redirect(url_for('dashboard'))
    return send_file(report_path, as_attachment=True, mimetype='text/markdown',
                     download_name=f"team_analysis_{analysis_id}.md")


@app.route('/take_test')
@login_required
def take_test():
    if current_user.role != 'employee':
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))
    if not current_user.test_link:
        flash('Тест не назначен', 'warning')
        return redirect(url_for('dashboard'))
    if datetime.now() > current_user.test_expires:
        flash('Срок тестирования истек', 'danger')
        return redirect(url_for('dashboard'))
    return redirect(current_user.test_link)


def init_db():
    with app.app_context():
        db.create_all()
        if not Question.query.filter_by(question_type='disc').first():
            create_disc_questions(1)
        if not Question.query.filter_by(question_type='eq').first():
            create_eq_questions(1)
        db.session.commit()


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
