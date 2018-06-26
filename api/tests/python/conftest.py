import pytest

from app import create_app
from app.models import db as _db
from config import TestConfig
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import MetaData, DropConstraint


@pytest.fixture(scope="session")
def app(request):
    """
    Returns session-wide application.
    """
    app = create_app(TestConfig)

    return app


@pytest.fixture(scope="session")
def client(app):
    """
    Returns session-wide Flask test client.
    """
    return app.test_client()


@pytest.fixture(scope="session")
def db(app, request):
    """
    Returns session-wide initialised database.
    Drops all existing tables - Meta follows Postgres FKs
    """
    with app.app_context():
        # Clear out any existing tables
        metadata = MetaData(_db.engine)
        metadata.reflect()
        for table in metadata.tables.values():
            for fk in table.foreign_keys:
                _db.engine.execute(DropConstraint(fk.constraint))
        metadata.drop_all()
        # create the tables
        _db.create_all()


@pytest.fixture(scope="function", autouse=True)
def session(app, db, request):
    """
    Returns function-scoped session.
    """
    with app.app_context():
        conn = _db.engine.connect()
        txn = conn.begin()

        options = dict(bind=conn, binds={})
        sess = _db.create_scoped_session(options=options)

        # establish  a SAVEPOINT just before beginning the test
        # (http://docs.sqlalchemy.org/en/latest/orm/session_transaction.html#using-savepoint)
        sess.begin_nested()

        @event.listens_for(sess(), 'after_transaction_end')
        def restart_savepoint(sess2, trans):
            # Detecting whether this is indeed the nested transaction of the test
            if trans.nested and not trans._parent.nested:
                # Handle where test DOESN'T session.commit(),
                sess2.expire_all()
                sess.begin_nested()

        _db.session = sess

        sql = text('select 1')
        sess.execute(sql)

        yield sess

        # Cleanup
        sess.remove()
        # This instruction rollsback any commit that were executed in the tests.
        txn.rollback()
        conn.close()
