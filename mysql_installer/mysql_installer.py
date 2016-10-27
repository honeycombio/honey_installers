#!/usr/bin/env python

import click
import os
import platform
import subprocess
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.basename(__file__), "..")))

from honey_installer import (HoneyInstaller, get_choice, get_version, Popen)

INSTALLER_NAME = "MySQL"
INSTALLER_VERSION = get_version() + "-" + platform.system().lower()
PARSER_MODULE = "mysql"
DEFAULT_DATASET = "MySQL"

MYSQL = ["mysql", "--silent", "--disable-column-names"]

TEAM_URL = "https://api.honeycomb.io/1/team_slug"


def _auth_mysql_cmd(cmd, username, password):
    """takes a command string and adds auth tokens if necessary"""
    if username != "":
        cmd.append("--user")
        cmd.append(username)
    if password != "":
        cmd.append("--password="+password)
    return cmd


def _find_log_file(username, password):
    """asks mysql for the location of the slow query log.
    """
    p = Popen(_auth_mysql_cmd(MYSQL + ["-e", "SELECT @@global.slow_query_log_file"], username, password),
              stdout=subprocess.PIPE)
    # ask for a list of databases
    slow_query_log_out = p.communicate()
    slow_query_log_file = slow_query_log_out[0].strip()
    # slow_query_log_file should be 0 (disabled) or 1 (enabled)

    return slow_query_log_file


class MysqlInstaller(HoneyInstaller):
    def __init__(self, writekey, dataset, honeytail, debug, log_filename, username, password):
        super(MysqlInstaller, self).__init__(INSTALLER_NAME, INSTALLER_VERSION,
                                             PARSER_MODULE,
                                             "", # we'll fill this in in the pre_*_hooks below
                                             writekey, dataset, DEFAULT_DATASET, honeytail, debug)
        self.log_filename = log_filename
        self.username = username
        self.password = password

    def fixup_and_suggest(self):
        click.echo("Connecting to local mysql and gathering logging details...")

        # test connection to local mysql, get creds if necessary
        # returned (username, passwd) tuple will be empty strings if not necessary.
        self.username, self.password = self._check_mysql_connection(self.username, self.password)

        self._check_and_set_slow_query_log(self.username, self.password)

    def _check_mysql_connection(self, username, password):
        """Tries to connect to local MySQL.
        If it fails, asks for MySQL connection creds"""
        # connect to mysql
        cmd = []
        cmd += MYSQL
        cmd += ["--user", username]
        if password != "":
            cmd += ["--password="+password]
        cmd += ["-e", "SELECT 1"]

        p = Popen(cmd, stdout=subprocess.PIPE)
        # throw out the result, just care that we could do it
        p.communicate()
        # see if it worked
        if p.returncode != 0:
            # we failed to connect to mysql. let's ask for connection details
            msg = "We failed to connect to mysql on localhost with the username '%s'".format(username)
            if password == "":
                msg += " and no password."
            else:
                msg += " and the supplied password."
            self.error(msg)
            choice = get_choice(["Try again with new credentials",
                                 "Skip checking for logging details and continue"],
                                "Which would you like to do?")
            if choice == 2:
                # skip checking and continue
                return ("", "")
            # we're going to ask for details and try again
            username = click.prompt("username for mysql")
            password = click.prompt("password for mysql", hide_input=True)
            p = Popen(MYSQL + ["--user", username, "--password="+password, "-e", "SELECT 1"], stdout=subprocess.PIPE)
            p.communicate()
            if p.returncode != 0:
                self.error("""Sorry, but we still couldn't connect to a local mysql.
This installer only works with mysql running on localhost:3306.
Bailing out.""")
                sys.exit()

        return (username, password)

    def _check_and_set_slow_query_log(self, username, password):
        """Asks for a list of all dbs, ignores test and local
        Gets the profile level of each db
        Suggests changing the profile level of each that's not 2 to 2
        Asks for permission to do so, do so if allowed, print how to do so if not
        """

        p = Popen(_auth_mysql_cmd(MYSQL + ["-e", "SELECT @@global.log_output"], username, password),
                  stdout=subprocess.PIPE)
        # check the log_output to make sure it's FILE (not TABLE)
        log_output_target_out = p.communicate()
        log_output_target = log_output_target_out[0].strip()
        # log_output_target should be FILE or TABLE or NONE
        if log_output_target == "TABLE":
            click.echo("""

We found that the "log_output" variable is set to "TABLE".

The MySQL connector currently only supports sending the slow query log to a
file. If you are interested in sending the slow query log to Honeycomb from a
table, please let us know at unicorns@honeycomb.io. We'd love to hear about it.

Please see https://dev.mysql.com/doc/refman/5.7/en/log-destinations.html for
more detail about the log_output variable and log file destinations.

Aborting...""")
            sys.exit(1)

        p = Popen(_auth_mysql_cmd(MYSQL + ["-e", "SELECT @@global.slow_query_log"], username, password),
                  stdout=subprocess.PIPE)
        # check the slow_query_log enabled flag
        slow_log_out = p.communicate()
        slow_log_state = int(slow_log_out[0].strip())
        # slow_log_state should be 0 (disabled) or 1 (enabled)

        p = Popen(_auth_mysql_cmd(MYSQL + ["-e", "SELECT @@global.long_query_time"], username, password),
                  stdout=subprocess.PIPE)
        # check the slow_query_log threshold
        long_query_time_out = p.communicate()
        long_query_time = float(long_query_time_out[0].strip())
        # long_query_time should be a float in seconds.

        if slow_log_state != 1 or long_query_time != 0.0 or log_output_target != "FILE":
            # we need to update one or both
            click.echo("""
We suggest enabling the slow query log and lowering the threshold for which
queries are considered "slow" in order to get the most out of your MySQL logs
(and the most value out of Honeycomb).

If you agree, we'll run:

    SET @@global.slow_query_log = 'ON';
    SET @@global.long_query_time = 0;
    SET @@global.log_output = 'FILE';
""")
            if click.confirm("Should we set the slow query log (Y) or skip it and continue (n)?", default=True):
                failed = False
                res = subprocess.call(_auth_mysql_cmd(MYSQL + ["-e", "SET @@global.slow_query_log = 'ON'"], username, password))
                if res != 0:
                    failed = True
                    click.echo("Failed to enable the slow query log.")
                res = subprocess.call(_auth_mysql_cmd(MYSQL + ["-e", "SET @@global.long_query_time = 0"], username, password))
                if res != 0:
                    failed = True
                    click.echo("Failed to set long_query_time to 0.")
                res = subprocess.call(_auth_mysql_cmd(MYSQL + ["-e", "SET @@global.log_output = 'FILE'"], username, password))
                if res != 0:
                    failed = True
                    click.echo("Failed to set log_output to FILE.")
                if failed:
                    click.echo("""
We'll continue to set up honeytail, but you should consider making changes to
your MySQL in order to get more interesting output in the slow query log.

You can read more about the slow query log here:
http://dev.mysql.com/doc/refman/5.7/en/slow-query-log.html

If you change your mind, you can run the above commands from a MySQL shell
anytime.

And/or update your my.cnf with the following to turn on slow query logging
permanently:

    slow_query_log = 1
    long_query_time = 0.0
    log_output = FILE
""")
                else:
                    click.echo("""
Great, we've gone ahead and made the changes in the running MySQL instance.
To make these changes permanent, modify your MySQL config to set the correct
slow query log/query threshold parameters.

The location of my.cnf varies by OS, but is often found near /etc/mysql/my.cnf
Add the following to your config:

    slow_query_log = 1
    long_query_time = 0.0
    log_output = FILE

After saving your changes, restart your MySQL instance.
""")
            else:
                click.echo("""
Ok, we'll skip changing the slow_query_log settings right now. This means that,
honeytail might not pick up new queries flowing through your MySQL instance.
""")
        else:
            self.success("Your current MySQL configuration looks great!")


    def find_log_file(self):
        return _find_log_file(self.username, self.password)

    def pre_backfill_hook(self):
        extra_flags = ""
        if self.username != "":
            extra_flags += " --mysql.user={}".format(self.username)
        if self.password != "":
            extra_flags += " --mysql.pass={}".format(self.password)
        self.parser_extra_flags = extra_flags

    def pre_tail_hook(self, after_backfill):
        extra_flags = ""
        if self.username != "":
            extra_flags += " --mysql.user={}".format(self.username)
        if self.password != "":
            extra_flags += " --mysql.pass={}".format(self.password)
        self.parser_extra_flags = extra_flags

        if not after_backfill:
            click.echo("""
In order to backfill later, use the following command:""")
            self.print_lines(self.get_backfill_lines(self.log_file))
            click.echo()

@click.command()
@click.option("--writekey", "-k", help="Your Honeycomb Writekey", default="")
@click.option("--dataset", "-d", help="Your Honeycomb Dataset", default=DEFAULT_DATASET)
@click.option("--file", "-f", "log_filename", help="mysql Log File")
@click.option("--honeytail", help="Honeytail location", default="honeytail")
@click.option("--username", help="mysql username", default="root")
@click.option("--password", help="mysql password", default="")
@click.option("--debug/--no-debug", help="Turn Debug mode on", default=False)
@click.version_option(INSTALLER_VERSION)
def start(writekey, dataset, log_filename, honeytail, username, password, debug):

    installer = MysqlInstaller(writekey, dataset, honeytail, debug, log_filename, username, password)
    installer.start()

if __name__ == "__main__":
    start()
