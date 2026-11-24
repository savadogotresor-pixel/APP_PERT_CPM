-- Schéma SQL de la base PERT/CPM (dialecte SQLite).
-- Généré depuis models.py ; la production utilise PostgreSQL via DATABASE_URL.
-- Les tables sont créées de façon idempotente par scripts/init_db.py.

CREATE TABLE user (
	id INTEGER NOT NULL, 
	username VARCHAR(80) NOT NULL, 
	email VARCHAR(120) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	is_admin BOOLEAN NOT NULL, 
	email_verifie BOOLEAN DEFAULT (1) NOT NULL, 
	code_verification_hash VARCHAR(255), 
	code_verification_expire DATETIME, 
	tentatives_code INTEGER DEFAULT 0 NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username), 
	UNIQUE (email)
);

CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(100), 
	details TEXT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES user (id)
);

CREATE TABLE project (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description TEXT, 
	date_reference DATE, 
	status VARCHAR(20), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE
);
CREATE INDEX ix_project_user_id ON project (user_id);
CREATE INDEX ix_project_user_id_created_at ON project (user_id, created_at);

CREATE TABLE alerte_risque (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	code_tache VARCHAR(50), 
	titre VARCHAR(200) NOT NULL, 
	niveau VARCHAR(20), 
	justification TEXT, 
	recommandation TEXT, 
	origine VARCHAR(20), 
	appreciation_ia TEXT, 
	statut VARCHAR(20), 
	commentaire TEXT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id)
);
CREATE INDEX ix_alerte_risque_project_id ON alerte_risque (project_id);

CREATE TABLE project_access (
	project_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	niveau VARCHAR(20), 
	PRIMARY KEY (project_id, user_id), 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE
);

CREATE TABLE scenario (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	nom VARCHAR(120) NOT NULL, 
	hypotheses TEXT NOT NULL, 
	resultats TEXT NOT NULL, 
	date_creation DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id)
);
CREATE INDEX ix_scenario_project_id ON scenario (project_id);

CREATE TABLE task (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	description TEXT, 
	duree INTEGER NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
);
CREATE INDEX ix_task_project_id ON task (project_id);
CREATE INDEX ix_task_project_id_batch ON task (project_id);

CREATE TABLE dependency (
	successor_id INTEGER NOT NULL, 
	predecessor_id INTEGER NOT NULL, 
	type_relation VARCHAR(4), 
	PRIMARY KEY (successor_id, predecessor_id), 
	CONSTRAINT uq_dependency_pair UNIQUE (successor_id, predecessor_id), 
	FOREIGN KEY(successor_id) REFERENCES task (id) ON DELETE CASCADE, 
	FOREIGN KEY(predecessor_id) REFERENCES task (id) ON DELETE CASCADE
);

-- Clé privée RSA persistée : elle doit rester secrète et ne jamais être
-- exposée par une route ni versionnée dans Git.
CREATE TABLE clef_crypto (
	nom VARCHAR(50) NOT NULL, 
	pem TEXT NOT NULL, 
	cree_le DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (nom)
);
