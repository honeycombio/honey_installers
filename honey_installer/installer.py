import click
import colors
from distutils.version import StrictVersion
import hashlib
import logging
import os
import platform
import re
import requests
import shutil
import stat
import subprocess
import sys
import urllib

def get_version():
    try:
        from honey_installer_version import version
        return version
    except:
        return "dev"
        
use_color = None
def bold(str):
    global use_color
    if use_color is None:
        use_color = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
    if use_color:
        return colors.bold(str)
    return str
    
BACKFILL_AND_TAIL = 1
ONLY_TAIL = 2
SHOW_COMMANDS = 3
 
HONEYTAIL_VERSION="1.127"
HONEYTAIL_CHECKSUM = {
    "Linux": "afc2aac444155cdd482b8e23b86ca34dd8a6fc53a7df9ddae66ba1289577ef87",
    #"Darwin": "68cfd0cdc8c016d3d8b62ff6d6388e0c8e24bd83174e45ada6c761c365aaa677",
}.get(platform.system(), None)

HONEYTAIL_URL = {
    "Linux": "https://honeycomb.io/download/honeytail/"+HONEYTAIL_VERSION,
    #"Darwin": "http://localhost:8080/honeytail"
}.get(platform.system(), None)


TEAM_URL = "https://api.honeycomb.io/1/team_slug"

def get_choice(choices, prompt):
    while True:
        for i, choice in enumerate(choices):
            click.echo("  [{}] {}".format(i + 1, choice))
        choice = click.prompt(prompt, type=int)
        if choice > 0 and choice <= len(choices):
            return choice

        click.echo("invalid choice, sorry.")

sizeK = 1024
sizeM = 1024 * sizeK
sizeG = 1024 * sizeM

def estimate_ingest_time(file_size, take_or_be):
    # these are totally pulled out of thin air and may be *way* off
    if file_size > 5 * sizeG:
        return "may " + take_or_be + " several hours"
    elif file_size > 1 * sizeG:
        return "may " + take_or_be + " a couple hours"
    elif file_size > 500 * sizeM:
        return "may " + take_or_be + " an hour"
    elif file_size > 1 * sizeM:
        return "may " + take_or_be + " several minutes"
    else:
        return "may " + take_or_be + " a couple minutes"

class HoneyInstaller(object):
    def __init__(self, installer_name, installer_version, parser_module, parser_extra_flags, writekey, dataset, default_dataset, honeytail_loc, debug):
        self.installer_name = installer_name
        self.installer_version = installer_version
        self.parser_module = parser_module
        self.parser_extra_flags = parser_extra_flags
        self.writekey = writekey
        self.dataset = dataset
        self.default_dataset = default_dataset
        self.honeytail_loc = honeytail_loc
        self.debug = debug
        
    def check_honeytail(self):
        if not os.path.isfile(self.honeytail_loc):
            if self.honeytail_loc == "honeytail":
                # the default location
                click.echo(bold("... couldn't find honeytail, downloading version %s" % HONEYTAIL_VERSION))
            else:
                # the user specified a location, so let's tell them we couldn't find it there
                click.echo(bold("... couldn't find honeytail at %s, downloading version %s to ./honeytail" % (self.honeytail_loc, HONEYTAIL_VERSION)))

            self.honeytail_loc = self.fetch_honeytail()
            return
        
        existing_version, existing_newer = self.check_honeytail_version()
        if not existing_newer:
            if self.honeytail_loc != "honeytail":
                click.echo(bold("... honeytail version at %s is too old (%s), downloading new version (%s) to ./honeytail." % (self.honeytail_loc, existing_version, HONEYTAIL_VERSION)))
            else:
                click.echo(bold("... honeytail version is too old (%s), downloading new version (%s)." % (existing_version, HONEYTAIL_VERSION)))

            self.honeytail_loc = self.fetch_honeytail()
            return

        click.echo(bold("... found existing honeytail binary."))
        click.echo()


    def check_honeytail_version(self):
        honeytail_cmd = os.path.abspath(self.honeytail_loc)
        try:
            verstring = subprocess.check_output([honeytail_cmd, "--version"], stderr=subprocess.STDOUT)
        except OSError:
            return "unknown", False
    
        verstring = re.sub(r'Honeytail version', '', verstring).strip()
        return verstring, StrictVersion(verstring) >= StrictVersion(HONEYTAIL_VERSION)

    def fetch_honeytail(self):
        # Mac doesn't ship with wget, we could use curl
        if not HONEYTAIL_URL:
            click.echo("""Sorry, only Linux is supported for {installer_name} auto configuration.
Please see the docs or ask for further assistance.
https://honeycomb.io/docs/send-data/agent/""".format(installer_name=self.installer_name))
            sys.exit(1)

        dest = "./honeytail"
        dest_tmp = dest + "-tmp"

        with open(dest_tmp, "wb") as fb:
            headers = {"User-Agent": self.get_user_agent()}
            resp = requests.get(HONEYTAIL_URL, stream=True, headers=headers)
            if resp.status_code != 200:
                click.echo("There was an error downloading honeytail. Please try again or let us know what happened.")
                try:
                    os.remove(dest_tmp)
                except OSError:
                    pass
                sys.exit(1)

            resp.raw.decode_content = True
            shutil.copyfileobj(resp.raw, fb)

        if HONEYTAIL_CHECKSUM:
            click.echo("Verifying the download.")
            hash = hashlib.sha256()
            with open(dest_tmp, "rb") as fh:
                while True:
                    chunk = fh.read(4096)
                    if not chunk:
                        break
                    hash.update(chunk)

            if hash.hexdigest() != HONEYTAIL_CHECKSUM:
                click.echo("The hash of the downloaded file didn't match the one on record.")
                click.echo("Please try again or ask for further assistance.")
                logging.error("Expecting : {} but received {}".format(HONEYTAIL_CHECKSUM, hash.hexdigest()))
                shutil.move(dest_tmp, dest+"-badchecksum")
                sys.exit(1)

        shutil.move(dest_tmp, dest)
        
        click.echo("Ensuring %s is executable." % dest)
        click.echo()
        os.chmod(dest, stat.S_IRWXU | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRGRP | stat.S_IROTH)
        return dest


    def get_user_agent(self):
        return "{installer_name}-installer/{installer_version}".format(installer_name=self.installer_name, installer_version=self.installer_version)

    
    def get_team_slug(self):
        '''calls out to Honeycomb to turn the writekey into the slug necessary to
        form the URL straight in to the dataset in the UI'''
        headers = {"X-Honeycomb-Team": self.writekey,
                   "User-Agent": self.get_user_agent()}
        resp = requests.get(TEAM_URL, headers=headers)
        if resp.status_code != 200:
            click.echo("There was an error resolving your Team Name. Please verify your write key and try again, or let us know what happened.")
            sys.exit(1)
            return resp.json()["team_slug"]

        
    def prompt_for_writekey_and_dataset(self):
        if self.writekey == "":
            self.writekey = click.prompt("What is your Honeycomb Write Key? (Available at https://ui.honeycomb.io/account)")
            click.echo()

        self.team_slug = self.get_team_slug()
            
        if self.dataset == self.default_dataset:
            self.dataset = click.prompt("What Honeycomb dataset should we send events to (will be created it not present)?", default=self.default_dataset)
            click.echo()


    def prompt_for_run_mode(self):
        file_size = os.stat(self.log_file).st_size

        click.echo("""
Honeytail is ready to start sending data.

By default, honeytail only parses new log lines (like `tail -f`).
It can also backfill existing logs, which can get you started with more data in the query tools faster.
""")

        estimate = estimate_ingest_time(file_size, "be")
        if estimate != "":
            click.echo("{log_file} size is {file_size} bytes, so it {estimate} before honeytail is sending real-time data.".format(
                log_file=self.log_file, file_size=file_size, estimate=estimate))
            click.echo()

        click.echo("How would you like to start the data flowing to honeycomb?")

        choice = get_choice(["Backfill {} and then switch to tailing".format(self.log_file),
                             "Only tail {}".format(self.log_file),
                             "Show commands and exit"],
                            "What would you like to do?")
        click.echo()
        return choice, file_size

    def prompt_for_log_file(self):
        log_file = click.prompt("please enter the path to your {} log file".format(self.installer_name))
        click.echo()
        if not os.path.isfile(log_file):
            click.echo("We were unable to locate a log file at {}".format(log_file))
            return None
        return log_file


    def print_lines(self, lines):
        click.echo()
        click.echo(bold("  {} \\".format(lines[0])))
        for x in lines[1:-1]:
            click.echo(bold("    {} \\".format(x)))
        click.echo(bold("    {}".format(lines[-1])))

        
    def get_tail_lines(self, log_file):
        honeytail_cmd = os.path.abspath(self.honeytail_loc)

        return [
            "{honeytail_cmd}".format(honeytail_cmd=honeytail_cmd),
            """--parser="{parser_module}" {parser_extra_flags}""".format(parser_module=self.parser_module, parser_extra_flags=self.parser_extra_flags),
            """--writekey="{writekey}" --dataset="{dataset}" """.format(writekey=self.writekey, dataset=self.dataset),
            """--file="{log_file}" """.format(log_file=log_file)
        ]


    def get_backfill_lines(self, log_file):
        honeytail_cmd = os.path.abspath(self.honeytail_loc)

        return [
            "{honeytail_cmd}".format(honeytail_cmd=honeytail_cmd),
            """--parser="{parser_module}" {parser_extra_flags}""".format(parser_module=self.parser_module, parser_extra_flags=self.parser_extra_flags),
            "--tail.read_from=beginning --tail.stop --backoff",
            """--writekey="{writekey}" --dataset="{dataset}" """.format(writekey=self.writekey, dataset=self.dataset),
            """--file="{log_file}" """.format(log_file=log_file)
        ]

    
    def backfill(self, file_size):
        """run honeytail against an existing log"""

        self.pre_backfill_hook()
            
        backfill_lines = self.get_backfill_lines(self.log_file)
        
        backfill_command = " ".join(backfill_lines)

        if self.debug:
            backfill_command += " --debug"

        click.echo("Backfilling by running the following command:")
        self.print_lines(backfill_lines)
        
        click.echo("""
Feel free to run the above command after replacing the --file argument with other,
rotated log files in order to backfill more data. You can run the command at any time.""")
        click.echo()

        estimate = estimate_ingest_time(file_size, "take")
        if estimate != "":
            estimate = "- " + estimate
        click.echo(bold("... Backfilling from {log_file} {estimate}".format(log_file=self.log_file, estimate=estimate)))
        subprocess.call(backfill_command, shell=True)
        click.echo(bold("... Done backfilling from {log_file}".format(log_file=self.log_file)))
        click.echo()


    def tail(self, after_backfill):
        self.pre_tail_hook(after_backfill)

        tail_lines = self.get_tail_lines(self.log_file)
        
        command = " ".join(tail_lines)

        if self.debug:
            command += " --debug"

        if after_backfill:
            msg = "Switching to real-time events by running the following command"
        else:
            msg = "Sending real-time events by running the following command"
        
        click.echo(msg)
        self.print_lines(tail_lines)

        click.echo("""
You can interrupt the installer at any point and run the above honeytail command yourself,
or add it to system startup scripts.
""")

        click.echo(bold("... Sending new data from {log_file}".format(log_file=self.log_file)))
        subprocess.call(command, shell=True)


    def show_commands(self):
        self.pre_show_commands_hook()
        
        tail_lines = self.get_tail_lines(self.log_file)
        backfill_lines = self.get_backfill_lines("<LOG_FILE_PATH>")

        click.echo("To tail and send real-time events from {}, run this command:".format(self.log_file))
        self.print_lines(tail_lines)

        click.echo()
        click.echo("To backfill from this or other rotated out logs, you can use this command:")
        self.print_lines(backfill_lines)

        click.echo()
        click.echo(bold("NOTE:") + " if you want to backfill and send real-time events from the same file, backfill first.")


    def hook(self):
        pass


    def pre_backfill_hook(self):
        pass


    def pre_tail_hook(self, after_backfill):
        pass


    def pre_show_commands_hook(self):
        pass


    def find_log_file(self):
        raise Exception("find_log_file not implemented in subclass")


    def start(self):
        title = "Honeytail {installer_name} installer".format(installer_name=self.installer_name)
        underlines = "-" * len(title)
        click.echo("""{title}
{underlines}

We're going to attempt to autoconfigure honeytail for your {installer_name} installation and start sending data.
""".format(title=title, underlines=underlines, installer_name=self.installer_name))
        
        self.check_honeytail()

        self.prompt_for_writekey_and_dataset()

        self.hook()
        
        self.log_file = self.find_log_file()
        if not self.log_file:
            click.echo("We were unable to locate a log file")
        while not self.log_file:
            self.log_file = self.prompt_for_log_file()

        click.echo(bold("... found log file at {log_file}".format(log_file=self.log_file)))
        
        mode, file_size = self.prompt_for_run_mode()

        if mode == SHOW_COMMANDS:
            self.show_commands()
            sys.exit()
        
        click.echo("""
Congratulations! You've set up honeytail to ingest your {installer_name} logs. Try running
a query against your new {installer_name} data:

    https://ui.honeycomb.io/{team_slug}/datasets/{dataset}
""".format(installer_name=self.installer_name, team_slug=self.team_slug, dataset=urllib.quote(self.dataset)))
        
        if mode == BACKFILL_AND_TAIL:
            self.backfill(file_size)
            self.tail(after_backfill=True)
        else: # mode == ONLY_TAIL
            self.tail(after_backfill=False)