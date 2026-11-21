"""Modèles de données et règles d'accès pour l'application PERT/CPM."""

from sqlalchemy import func

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    projects = db.relationship(
        'Project',
        backref='owner',
        cascade='all, delete-orphan',
        lazy=True,
    )
    project_accesses = db.relationship(
        'ProjectAccess',
        backref='utilisateur',
        cascade='all, delete-orphan',
        lazy=True,
    )

    def set_password(self, password):
        """Stocke un hash de mot de passe (jamais le clair)."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Vérifie un mot de passe en clair contre le hash stocké."""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Project(db.Model):
    __tablename__ = 'project'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    date_reference = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='actif')
    created_at = db.Column(db.DateTime, server_default=func.now())

    tasks = db.relationship(
        'Task',
        backref='project',
        cascade='all, delete-orphan',
        lazy=True,
    )
    accesses = db.relationship(
        'ProjectAccess',
        backref='project',
        cascade='all, delete-orphan',
        lazy=True,
    )

    def __repr__(self):
        return f'<Project {self.name}>'


class Task(db.Model):
    __tablename__ = 'task'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    code = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text)
    duree = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())

    dependances = db.relationship(
        'Dependency',
        foreign_keys='Dependency.successor_id',
        backref='tache_cible',
        cascade='all, delete-orphan',
        lazy=True,
    )

    def __repr__(self):
        return f'<Task {self.code}>'


class Dependency(db.Model):
    __tablename__ = 'dependency'
    __table_args__ = (
        db.UniqueConstraint(
            'successor_id', 'predecessor_id', name='uq_dependency_pair'
        ),
    )

    successor_id = db.Column(
        db.Integer, db.ForeignKey('task.id', ondelete='CASCADE'), primary_key=True
    )
    predecessor_id = db.Column(
        db.Integer, db.ForeignKey('task.id', ondelete='CASCADE'), primary_key=True
    )
    type_relation = db.Column(db.String(4), default='FS')

    predecessor = db.relationship(
        'Task',
        foreign_keys=[predecessor_id],
        backref='dependances_sortantes',
    )

    def __repr__(self):
        return f'<Dependency {self.predecessor_id} -> {self.successor_id}>'


class ProjectAccess(db.Model):
    """Accès explicite d'un utilisateur à un projet qui n'est pas le sien."""

    __tablename__ = 'project_access'

    project_id = db.Column(
        db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), primary_key=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True
    )
    niveau = db.Column(db.String(20), default='lecture')


class AuditLog(db.Model):
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(100))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=func.now())

    user = db.relationship('User', backref='audit_logs')


class Scenario(db.Model):
    """Hypothèses et résultats d'une simulation PERT/CPM non appliquée."""

    __tablename__ = 'scenario'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'),
                           nullable=False, index=True)
    nom = db.Column(db.String(120), nullable=False)
    hypotheses = db.Column(db.Text, nullable=False, default='{}')
    resultats = db.Column(db.Text, nullable=False, default='{}')
    date_creation = db.Column(db.DateTime, server_default=db.func.now())
    projet = db.relationship('Project', backref='scenarios')


class AlerteRisque(db.Model):
    """Alerte de risque calculée par règles, éventuellement commentée par l'IA."""

    __tablename__ = 'alerte_risque'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'),
                           nullable=False, index=True)
    code_tache = db.Column(db.String(50))
    titre = db.Column(db.String(200), nullable=False)
    niveau = db.Column(db.String(20), default='indicatif')
    justification = db.Column(db.Text)
    recommandation = db.Column(db.Text)
    origine = db.Column(db.String(20), default='regle')  # 'regle' ou 'regle+ia'
    appreciation_ia = db.Column(db.Text)  # None si aucun fournisseur IA
    statut = db.Column(db.String(20), default='en_attente')  # en_attente|confirme|rejete
    commentaire = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    projet = db.relationship('Project', backref='alertes_risque')


def project_to_taches(project):
    """Convertit les tâches d'un projet au format attendu par le moteur PERT.

    Retourne ``{code: {'description': ..., 'duree': ..., 'depend_de': [codes]}}``.
    """
    if project is None:
        return {}

    par_id = {tache.id: tache for tache in project.tasks}
    resultat = {}
    for tache in project.tasks:
        depend_de = []
        for dep in tache.dependances:
            precedent = dep.predecessor or par_id.get(dep.predecessor_id)
            if precedent is None and dep.predecessor_id is not None:
                precedent = db.session.get(Task, dep.predecessor_id)
            if precedent is not None:
                depend_de.append(precedent.code)
        resultat[tache.code] = {
            'description': tache.description or '',
            'duree': tache.duree,
            'depend_de': depend_de,
        }
    return resultat


def user_can_access(project, user):
    """Vrai si l'utilisateur peut voir ce projet (propriétaire, admin ou accès explicite)."""
    if project is None or user is None:
        return False

    if getattr(user, 'is_admin', False):
        return True

    project_id = getattr(project, 'id', None)
    user_id = getattr(user, 'id', None)

    if project_id is not None and user_id is not None:
        if project.user_id == user_id:
            return True
        return (
            db.session.query(ProjectAccess)
            .filter_by(project_id=project_id, user_id=user_id)
            .first()
            is not None
        )

    # Objets encore transitoires (pas de primary key) : comparer les relations.
    owner = getattr(project, 'owner', None)
    return owner is not None and owner is user


def init_db(app):
    """Crée les tables dans le contexte applicatif Flask."""
    with app.app_context():
        db.create_all()
