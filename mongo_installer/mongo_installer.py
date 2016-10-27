#!/usr/bin/env python

import click
import json
import os
import platform
import semver
import subprocess
import sys
import yaml

sys.path.append(os.path.abspath(os.path.join(os.path.basename(__file__), "..")))

from honey_installer import (HoneyInstaller, get_choice, get_version, Popen)

INSTALLER_NAME = "mongo"
INSTALLER_VERSION = get_version() + "-" + platform.system().lower()
PARSER_MODULE = "mongo"
DEFAULT_DATASET = "Mongo"

CONFIG_LOCS = ["/etc/mongod.conf",
               "/etc/mongodb.conf",
               "/usr/local/etc/mongod.conf",
               "/usr/local/etc/mongodb.conf",
               "/opt/mongo/etc/mongodb.conf",
               "/opt/mongo/mongodb.conf",
               ]

LOG_LOCS = ["/var/log/mongodb/mongodb.log",
            "/var/log/mongodb/mongod.log",
            "/usr/local/var/log/mongodb/mongodb.log",
            ]


def _auth_mongo_cmd(cmd, username, password, auth_db):
    """takes a command string and adds auth tokens if necessary"""
    if username != "":
        cmd.append("--username")
        cmd.append(username)
    if password != "":
        cmd.append("--password")
        cmd.append(password)
    if auth_db != "":
        cmd.append("--authenticationDatabase")
        cmd.append(auth_db)
    return cmd


def _find_log_file():
    """searches the config file for logpath, or if not found, searches the filesystem.
    """
    config_file = ""
    logpath = ""
    for loc in CONFIG_LOCS:
        if os.path.isfile(loc):
            # found config file
            config_file = loc
            # try and parse yaml. If we can, we'll use it.
            with open(config_file, 'r') as stream:
                try:
                    cf = yaml.load(stream)
                    logpath = cf["systemLog"]["path"]
                    # if we make it here, we've found ourselves a path
                    break
                except (KeyError, TypeError, yaml.YAMLError):
                    # failed to open it as yaml, let's try grep
                    p = Popen(["grep", "^ *logpath", config_file], stdout=subprocess.PIPE)
                    logpath_line = p.communicate()
                    try:
                        logpath = logpath_line[0].strip().split("=")[1]
                    except:
                        config_file=""
                        continue
                    break
    if logpath != "" and os.path.isfile(logpath):
        # found the config and a logfile within the config
        return (config_file, logpath)
    for log in LOG_LOCS:
        if os.path.isfile(log):
            # didn't find a config with a logfile, but did find a logfile
            return (None, log)
    # found neither a config with a logfile or a logfile
    return (None, None)


class MongoInstaller(HoneyInstaller):
    def __init__(self, writekey, dataset, honeytail, debug, log_filename):
        super(MongoInstaller, self).__init__(INSTALLER_NAME, INSTALLER_VERSION, PARSER_MODULE, "--mongo.log_partials", writekey, dataset, DEFAULT_DATASET, honeytail, debug)
        self.log_filename = log_filename


    def fixup_and_suggest(self):
        # check the mongo version, suggest upgrading if too old
        version = self._check_mongo_version()

        # test connection to local mongo, get creds if necessary
        # returned (username, passwd) tuple will be empty strings if not necessary.
        mongo_username, mongo_password, mongo_auth_db = self._check_mongo_connection()

        if (mongo_username, mongo_password, mongo_auth_db) != (None, None, None):
            self._check_and_fix_mongo_logging_level(mongo_username, mongo_password, mongo_auth_db, version)

        click.echo()

    def _check_mongo_version(self):
        """check the version of mongo they're running. Suggest upgrading if 2.4.
        return the version number for later use."""
        p = Popen(["mongod", "--version"], stdout=subprocess.PIPE)
        full_verstring = p.communicate()
        if p.returncode != 0:
            self.error("Failed to determine the version of mongod you're running.")
            click.echo("Checking the mongo client version instead.")
            p = Popen(["mongo", "--version"], stdout=subprocess.PIPE)
            full_verstring = p.communicate()
            if p.returncode != 0:
                self.error("Unable to determine mongo version.")
                self.error("""Sorry, but we still couldn't connect to a local mongo.
This installer only works on the machine running mongo.
Bailing out.""")
                sys.exit()
            # eg "MongoDB shell version: 2.4.9"
            version = full_verstring[0].split()[3]
        else:
            # eg "db version v2.4.9"
            version = full_verstring[0].split()[2].lstrip('v')

        if semver.compare(version, "2.6.0") < 0:
            # 2.4
            self.warn("""You're running an old version of mongo ({}). The most important reasons
to move to at least version 2.6 are:
    * per-collection write locks
    * the actual query is logged so we can analyze it

We can carry on with the installation process for now, but strongly recommend
that you upgrade mongo.
""".format(version))

        return version


    def _check_mongo_connection(self):
        """Tries to connect to local mongo.
        If it fails, asks for mongo connection creds"""
        click.echo("Connecting to local mongo to check logging levels")
        # connect to mongo
        p = Popen(["mongo", "--quiet"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # ask for a list of databases
        p.communicate("show dbs")
        # see if it worked
        username = ""
        password = ""
        auth_db = ""
        if p.returncode != 0:
            self.error("Failed to connect to mongo on localhost with no username or password.")
            click.echo()

        while p.returncode != 0:
            # we failed to connect to mongo. let's ask for connection details
            choice = get_choice(["Enter connection details for your local mongo",
                                 "Skip checking for logging levels and continue"],
                                "What would you like to do?")
            if choice == 2:
                # skip checking and continue
                return (None, None, None)
            click.echo()
            # we're going to ask for details and try again
            username = click.prompt("  Mongo username")
            password = click.prompt("  Mongo password", hide_input=True)
            auth_db = click.prompt("  Mongo authentication database", default="admin")
            p = Popen(["mongo", "--quiet", "--username", username, "--password", password, "--authenticationDatabase", auth_db], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p.communicate("show dbs")
            if p.returncode == 0:
                break

            click.echo()
            self.error("Failed to connect to mongo on localhost with supplied username or password.")
            click.echo()

        return (username, password, auth_db)

    def _check_and_fix_mongo_logging_level(self, username, password, auth_db, version):
        """Asks for a list of all dbs, ignores test and local
        Gets the profile level of each db
        Suggests changing the profile level of each that's not 2 to 2
        Asks for permission to do so, do so if allowed, print how to do so if not
        """
        p = Popen(_auth_mongo_cmd(["mongo", "--quiet"], username, password, auth_db),
                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        # ask for a list of databases
        dblist = p.communicate("show dbs")
        # dblist is now a tuple that looks like this:
        # ('btest\t0.203125GB\nlocal\t0.078125GB\ntest\t0.078125GB\n', None)
        # let's grab the db names
        dbs = list()
        for dbusage in dblist[0].strip().split('\n'):
            db = dbusage.split()[0]
            if db != "local" and db != "test":
                dbs.append(db)

        # for each db, let's get the logging level.
        db_levels = dict()
        for db in dbs:
            p = Popen(_auth_mongo_cmd(["mongo", db, "--quiet"], username, password, auth_db),
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            log_level = p.communicate("db.getProfilingStatus()")
            try:
                pstatus = json.loads(log_level[0].strip())
                db_levels[db] = (pstatus["was"], pstatus["slowms"])
            except (KeyError, ValueError):
                # failed to parse the json or something. just carry on.
                continue

        dbs_to_change = list()
        for db, level in db_levels.items():
            if level[0] != 2 or level[1] != -1:
                dbs_to_change.append(db)

        # If there are any dbs without full query logging turned on, tell the user
        # and ask if we can change them.
        if len(dbs_to_change) != 0:
            click.echo("We suggest enabling full query logging on all databases you're interested in tracking in Honeycomb.")
            click.echo("More detail on query logging is available here: https://docs.mongodb.com/manual/reference/command/profile/#dbcmd.profile")
            click.echo("The following databases don't have full query logging turned on:")
            click.echo()
            for db in dbs_to_change:
                click.echo("\t{}".format(db))
            click.echo()
            click.echo("If you agree, we'll run:")
            click.echo()
            click.echo("\tdb.setProfilingLevel(2, -1)")
            click.echo()

            # change logging level if we're allowed
            if click.confirm("Would you like us to enable full query logging on these databases?", default=True):
                click.echo()
                for db in dbs_to_change:
                    click.echo("running db.setProfilingLevel(2, -1) on {} database to enable full logging...".format(db))
                    p = Popen(_auth_mongo_cmd(["mongo", db, "--quiet"], username, password, auth_db),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                    log_level = p.communicate("db.setProfilingLevel(2, -1)")
                self.success("Full query logging enabled")
                click.echo()
            click.echo("To permanently enact this change, add the following to your mongo config file:")
            # TODO verify this is correct
            if semver.compare(version, "2.6.0") < 0:
                # 2.4
                click.echo("\tprofile = 2")
            else:
                # 2.6+
                click.echo("\toperationProfiling:\n\t\tslowOpThresholdMs: -1\n\t\tmode: all")
            click.echo()

    def find_log_file(self):
        config, log_file = _find_log_file()
        if not log_file:
            if self.log_filename and os.path.isfile(self.log_filename):
                log_file = self.log_filename

        return log_file

@click.command()
@click.option("--writekey", "-k", help="Your Honeycomb Writekey", default="")
@click.option("--dataset", "-d", help="Your Honeycomb Dataset", default=DEFAULT_DATASET)
@click.option("--file", "-f", "log_filename", help="Mongo Log File")
@click.option("--honeytail", help="Honeytail location", default="honeytail")
@click.option("--debug/--no-debug", help="Turn Debug mode on", default=False)
@click.version_option(INSTALLER_VERSION)
def start(writekey, dataset, log_filename, honeytail, debug):

    installer = MongoInstaller(writekey, dataset, honeytail, debug, log_filename)
    installer.start()

if __name__ == "__main__":
    start()
