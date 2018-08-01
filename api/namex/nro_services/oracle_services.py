from flask import current_app, _app_ctx_stack
from datetime import datetime
import cx_Oracle

from .exceptions import NROServicesError


class NROServices(object):
    """Provides services to change the legacy NRO Database
       For ease of use, following the style of a Flask Extension
    """

    def __init__(self, app=None):
        """initializer, supports setting the app context on instantiation"""
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """setup for the extension
        :param app: Flask app
        :return: naked
        """
        self.app = app
        app.teardown_appcontext(self.teardown)

    def teardown(self, exception):
        # the oracle session pool will clean up after itself
        #
        # ctx = _app_ctx_stack.top
        # if hasattr(ctx, 'nro_oracle_pool'):
        #     ctx.nro_oracle_pool.close()
        pass

    def _create_pool(self):
        """create the cx_oracle connection pool from the Flask Config Environment

        :return: an instance of the OCI Session Pool
        """
        # this uses the builtin session / connection pooling provided by
        # the Oracle OCI driver
        # setting threaded =True wraps the underlying calls in a Mutex
        # so we don't have to that here

        return cx_Oracle.SessionPool(user=current_app.config.get('NRO_USER'),
                                  password=current_app.config.get('NRO_PASSWORD'),
                                  dsn='{0}:{1}/{2}'.format(current_app.config.get('NRO_HOST'),
                                                           current_app.config.get('NRO_PORT'),
                                                           current_app.config.get('NRO_DB_NAME')
                                                           ),
                                  min=1,
                                  max=10,
                                  increment=1,
                                  connectiontype=cx_Oracle.Connection,
                                  threaded=True,
                                  getmode=cx_Oracle.SPOOL_ATTRVAL_NOWAIT,
                                  waitTimeout=1500,
                                  timeout=3600
                                  )

    @property
    def connection(self):
        """connection property of the NROService
        If this is running in a Flask context,
        then either get the existing connection pool or create a new one
        and then return an acquired session
        :return: cx_Oracle.connection type
        """
        ctx = _app_ctx_stack.top
        if ctx is not None:
            if not hasattr(ctx, 'nro_oracle_pool'):
                ctx._nro_oracle_pool = self._create_pool()
            return ctx._nro_oracle_pool.acquire()

    def get_last_update_timestamp(self, nro_request_id):
        """Gets a datetime object that holds the last time and part of the NRO Request was modified

        :param nro_request_id: NRO request.request_id for the request we want to enquire about \
                               it DOES NOT use the nr_num, as that requires yet another %^&$# join and runs a \
                               couple of orders of magnitude slower. (really nice db design - NOT)
        :return: (datetime) the last time that any part of the request was altered
        :raise: (NROServicesError) with the error information set
        """

        try:
            cursor = self.connection.cursor()

            cursor.execute("""
                SELECT last_update
                FROM namex_req_instance_max_event
                WHERE request_id = :req_id"""
                ,req_id=nro_request_id)

            row = cursor.fetchone()

            if row:
                return row[0]

            return None

        except Exception as err:
            current_app.logger.error(err.with_traceback(None))
            raise NROServicesError({"code": "unable_to_get_timestamp",
                                    "description": "Unable to get the last timestamp for the NR in NRO"}, 500)

    def set_request_status_to_h(self, nr_num, examiner_username ):
        """Sets the status of the Request in NRO to "H"

        :param nr_num: (str) the name request number, of the format "NR 9999999"
        :param examiner_username: (str) any valid string will work, but it should be the username from Keycloak
        :return: naked
        :raise: (NROServicesError) with the error information set
        """

        try:
            con = self.connection
            con.begin() # explicit transaction in case we need to do other things than just call the stored proc
            try:
                cursor = con.cursor()

                # set the fqpn if the schema is required, which is set y the deployer/configurator
                # if the environment variable is missing from the Flask Config, then skip setting it.
                if current_app.config.get('NRO_SCHEMA'):
                    proc_name = '{}.nro_datapump_pkg.name_examination'.format(current_app.config.get('NRO_SCHEMA'))
                else:
                    proc_name = 'nro_datapump_pkg.name_examination'

                proc_vars = [nr_num,           # p_nr_number
                            'H',               # p_status
                            '',               # p_expiry_date - mandatory, but ignored by the proc
                            '',               # p_consent_flag- mandatory, but ignored by the proc
                            examiner_username, # p_examiner_id
                            ]

                # Call the name_examination procedure to save complete decision data for a single NR
                cursor.callproc(proc_name, proc_vars)

                con.commit()

            except cx_Oracle.DatabaseError as exc:
                error, = exc.args
                current_app.logger.error("NR#:", nr_num, "Oracle-Error-Code:", error.code)
                if con:
                    con.rollback()
                raise NROServicesError({"code": "unable_to_set_state",
                        "description": "Unable to set the state of the NR in NRO"}, 500)
            except Exception as err:
                current_app.logger.error("NR#:", nr_num, err.with_traceback(None))
                if con:
                    con.rollback()
                raise NROServicesError({"code": "unable_to_set_state",
                                        "description": "Unable to set the state of the NR in NRO"}, 500)
        #
        except Exception as err:
            # something went wrong, roll it all back
            current_app.logger.error("NR#:", nr_num, err.with_traceback(None))
            raise NROServicesError({"code": "unable_to_set_state",
                                    "description": "Unable to set the state of the NR in NRO"}, 500)

        return None