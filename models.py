"""Modèles de données et règles d'accès pour l'application PERT/CPM."""

from sqlalchemy import func, inspect, true

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

    # Vérification e-mail. ``server_default=true()`` est indispensable : les
    # comptes déjà présents en production doivent rester connectables après la
    # migration (ils sont réputés vérifiés). Les nouveaux comptes reçoivent
    # ``False`` via le défaut Python tant que le code n'est pas validé.
    email_verifie = db.Column(
        db.Boolean, default=False, nullable=False, server_default=true()
    )
    # Uniquement le hash HMAC-SHA256 du code : jamais le code en clair en base.
    code_verification_hash = db.Column(db.String(255), nullable=True)
    code_verification_expire = db.Column(db.DateTime, nullable=True)
    tentatives_code = db.Column(
        db.Integer, default=0, nullable=False, server_default=db.text('0')
    )

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

    # Index explicite pour les requêtes groupées par propriétaire et par date
    # de création : aucune colonne n'est modifiée.
    __table_args__ = (
        db.Index('ix_project_user_id_created_at', 'user_id', 'created_at'),
    )

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

    # Accès groupé aux tâches d'un projet (lots de suppression/export).
    __table_args__ = (
        db.Index('ix_task_project_id_batch', 'project_id'),
    )

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


def _migrer_colonnes_verification(engine):
    """Ajoute, si elles manquent, les colonnes de vérification à ``user``.

    Idempotente : chaque colonne est inspectée avant l'``ALTER TABLE``. Le
    dialecte SQLAlchemy compile les types (SQLite et PostgreSQL) et le
    ``DEFAULT TRUE`` garantit que les utilisateurs existants restent vérifiés,
    donc connectables, après le déploiement.
    """
    colonnes_existantes = {
        colonne['name'] for colonne in inspect(engine).get_columns('user')
    }
    dialecte = engine.dialect
    definitions = (
        (
            'email_verifie',
            '{} NOT NULL DEFAULT {}'.format(
                db.Boolean().compile(dialect=dialecte),
                true().compile(dialect=dialecte),
            ),
        ),
        ('code_verification_hash', db.String(255).compile(dialect=dialecte)),
        ('code_verification_expire', db.DateTime().compile(dialect=dialecte)),
        (
            'tentatives_code',
            '{} NOT NULL DEFAULT 0'.format(
                db.Integer().compile(dialect=dialecte)
            ),
        ),
    )
    for nom, definition in definitions:
        if nom in colonnes_existantes:
            continue
        with engine.begin() as connexion:
            connexion.execute(db.text(
                f'ALTER TABLE "user" ADD COLUMN "{nom}" {definition}'
            ))


def init_db(app):
    """Crée les tables puis migre la table ``user`` si nécessaire."""
    with app.app_context():
        db.create_all()
        _migrer_colonnes_verification(db.engine)
