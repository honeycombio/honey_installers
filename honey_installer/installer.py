import click
from distutils.version import StrictVersion
import emoji
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

from honeytail_version import (HONEYTAIL_VERSION, HONEYTAIL_CHECKSUM)

def get_version():
    try:
        from honey_installer_version import version
        return version
    except:
        return "dev"

BACKFILL_AND_TAIL = 1
ONLY_BACKFILL = 2
ONLY_TAIL = 3
SHOW_COMMANDS = 4

HONEYTAIL_URL = {
    "Linux": "https://honeycomb.io/download/honeytail/linux/"+HONEYTAIL_VERSION,
    "Darwin": "https://honeycomb.io/download/honeytail/darwin/"+HONEYTAIL_VERSION
}.get(platform.system(), None)


TEAM_URL = "https://api.honeycomb.io/1/team_slug"

# this env hack is because pyinstallers creates its own LD_LIBRARY_PATH
# which, when the script is run on a different linux distro, fails to load
# some libraries. This unsets it and gets around that problem.
def replace_subprocess_env(**kwargs):
    env = None
    if 'env' in kwargs:
        env = kwargs[env]
    if env is None:
        env = os.environ
    env = {k:v for k,v in os.environ.iteritems() if k != 'LD_LIBRARY_PATH' and k != 'DYLD_LIBRARY_PATH'}
    kwargs['env'] = env
    return kwargs

def Popen(args, **kwargs):
    try:
        kwargs = replace_subprocess_env(**kwargs)
        p = subprocess.Popen(args, **kwargs)
    except OSError as e:
        click.echo("Failed to run {}: {}".format(args, e))
        sys.exit(1)
    return p

def check_output(args, **kwargs):
    kwargs = replace_subprocess_env(**kwargs)
    return subprocess.check_output(args, **kwargs)

def get_choice(choices, prompt):
    while True:
        for i, choice in enumerate(choices):
            click.secho("  [{}] ".format(i+1), nl=False, bold=True)
            click.echo(choice)
        choice = click.prompt(prompt, type=int)
        if choice > 0 and choice <= len(choices):
            return choice

        click.echo("invalid choice, sorry.")

sizeK = 1024
sizeM = 1024 * sizeK
sizeG = 1024 * sizeM

def estimate_ingest_time(file_size, take_or_be):
    # these are totally pulled out of thin air and may be *way* off
    estimates = [
        (5 * sizeG, " several hours"),
        (1 * sizeG, " a couple hours"),
        (500 * sizeM, " an hour"),
        (1 * sizeM, " several minutes"),
    ]
    for est in estimates:
        if file_size > est[0]:
            return "may " + take_or_be + est[1]
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

    def success(self, msg):
        click.secho(emoji.emojize(":heavy_check_mark: " + msg), fg="green")

    def warn(self, msg):
        click.secho(msg, fg="yellow")

    def error(self, msg):
        click.secho(msg, fg="red")

    def check_honeytail(self):
        """make sure we have a usable honeytail.  will use the user-supplied
        executable if its version is >= HONEYTAIL_VERSION.  Otherwise, fetches
        a new one."""
        if not os.path.isfile(self.honeytail_loc):
            if self.honeytail_loc == "honeytail":
                # the default location
                click.echo("Downloading honeytail version {}.".format(HONEYTAIL_VERSION))
            else:
                # the user specified a location, so let's tell them we couldn't find it there
                click.echo("Couldn't find honeytail at {}.".format(self.honeytail_loc))
                click.echo("Downloading honeytail version {} to ./honeytail.".format(HONEYTAIL_VERSION))

            self.honeytail_loc = self.fetch_honeytail()
            return

        existing_version, existing_newer = self.check_honeytail_version()
        if existing_newer:
            self.success("Found usable honeytail binary (version {})".format(existing_version))
            click.echo()
            return

        if self.honeytail_loc != "honeytail":
            click.echo("Honeytail version at {} is too old ({}).".format(self.honeytail_loc, existing_version))
            click.echo("Downloading new version ({}) to ./honeytail.".format(HONEYTAIL_VERSION))
        else:
            click.echo("Honeytail version is too old ({}).".format(existing_version))
            click.echo("Downloading new version of honeytail ({})".format(HONEYTAIL_VERSION))

        self.honeytail_loc = self.fetch_honeytail()


    def check_honeytail_version(self):
        """runs honeytail_cmd --version and compares the version returned to
        HONEYTAIL_VERSION.  Returns a tuple of (version_string, version_newer)
        where version_newer is True if version_string compares >= to HONEYTAIL_VERSION."""
        honeytail_cmd = os.path.abspath(self.honeytail_loc)
        try:
            verstring = check_output([honeytail_cmd, "--version"], stderr=subprocess.STDOUT)

            verstring = re.sub(r'Honeytail version', '', verstring).strip()
            if verstring == "dev":
                return verstring, True
            return verstring, StrictVersion(verstring) >= StrictVersion(HONEYTAIL_VERSION)
        except OSError:
            return "unknown", False

    def fetch_honeytail(self):
        if not HONEYTAIL_URL:
            self.error("""\
Sorry, {installer_name} auto configuration is not supported for {platform}.
Please see the docs or ask for further assistance.
https://honeycomb.io/docs/send-data/agent/""".format(installer_name=self.installer_name, platform=platform.system()))
            sys.exit(1)

        return self.fetch_file("honeytail", HONEYTAIL_URL, HONEYTAIL_CHECKSUM, ensure_exec=True)

    def fetch_file(self, name, url, checksum=None, ensure_exec=False):
        """downloads the file from url and saves to ./name, optionally checking against
           a sha256 hash and making executable"""
        dest = "./" + name
        dest_tmp = dest + "-tmp"

        with open(dest_tmp, "wb") as fb:
            headers = {"User-Agent": self.get_user_agent()}
            resp = requests.get(url, stream=True, headers=headers)
            if resp.status_code != 200:
                self.error("There was an error downloading {}. Please try again or let us know what happened.".format(name))
                if self.debug:
                    click.secho("response status code = {}".format(resp.status_code), bold=True)
                try:
                    os.remove(dest_tmp)
                except OSError:
                    pass
                sys.exit(1)

            resp.raw.decode_content = True
            chunk_size=65536
            with click.progressbar(length=int(resp.headers['Content-length']), show_percent=True, width=50) as bar:
                for chunk in resp.iter_content(chunk_size):
                    fb.write(chunk)
                    bar.update(len(chunk))

        if checksum:
            hash = hashlib.sha256()
            with open(dest_tmp, "rb") as fh:
                while True:
                    chunk = fh.read(4096)
                    if not chunk:
                        break
                    hash.update(chunk)

            if hash.hexdigest() != checksum:
                self.error("The hash of the downloaded file didn't match the one on record.")
                self.error("Please try again or ask for further assistance.")
                logging.error("Expecting : {} but received {}".format(checksum, hash.hexdigest()))
                shutil.move(dest_tmp, dest+"-badchecksum")
                sys.exit(1)
            self.success("Download verified")

        shutil.move(dest_tmp, dest)

        if ensure_exec:
            os.chmod(dest, stat.S_IRWXU | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRGRP | stat.S_IROTH)

        click.echo()

        return dest


    def get_user_agent(self):
        return "{installer_name}-installer/{installer_version}".format(installer_name=self.installer_name, installer_version=self.installer_version)


    def get_team_slug(self):
        """calls out to Honeycomb to turn the writekey into the slug necessary to
        form the URL straight in to the dataset in the UI"""
        headers = {"X-Honeycomb-Team": self.writekey,
                   "User-Agent": self.get_user_agent()}
        resp = requests.get(TEAM_URL, headers=headers)
        if resp.status_code != 200:
            self.error("There was an error resolving your Team Name. Please verify your write key and try again, or let us know what happened.")
            self.error("\t" + resp.text)
            sys.exit(1)
        team_slug = resp.json()["team_slug"]
        self.success("Great, found your team: {}".format(team_slug))
        return team_slug


    def prompt_for_writekey_and_dataset(self):
        if self.writekey == "":
            self.writekey = click.prompt("What is your Honeycomb Write Key? (Available at https://ui.honeycomb.io/account)")
        self.writekey = self.writekey.strip()
        if not re.match('^[a-f0-9]+$', self.writekey):
            click.echo("Write Key {} contains unexpected characters - it should be a 32 character hexadecimal string. Please try again.".format(self.writekey))
            sys.exit(1)
        if len(self.writekey) != 32:
            click.echo("Write Key {} is not the expected length - it should be a 32 character hexadecimal string. Please try again.".format(self.writekey))
            sys.exit(1)
        self.team_slug = self.get_team_slug()

        if self.dataset == self.default_dataset:
            self.dataset = click.prompt("Which Honeycomb dataset should we send events to? (It'll be created if it doesn't already exist)", default=self.default_dataset)


    def prompt_for_run_mode(self):
        file_size = os.stat(self.log_file).st_size

        click.echo("""
Honeytail is ready to start sending data.

By default, honeytail only parses new log lines (like `tail -f`).
It can also backfill existing logs, which can get you started with more data in the query tools faster.
""")

        estimate = estimate_ingest_time(file_size, "be")
        if estimate != "":
            click.echo("{log_file} size is {file_size} bytes,".format(log_file=self.log_file, file_size=file_size))
            click.echo("so if you decide to backfill, it {estimate} before honeytail".format(estimate=estimate))
            click.echo("is sending real-time data.")
            click.echo()

        click.echo("How would you like to start the data flowing to honeycomb?")

        choice = get_choice(["Backfill {} and then switch to tailing".format(self.log_file),
                             "Only backfill {}".format(self.log_file),
                             "Only tail {}".format(self.log_file),
                             "Show commands and exit"],
                            "Which would you like to do?")
        click.echo()
        return choice, file_size

    def prompt_for_log_file(self):
        log_file = click.prompt("Please enter the path to your {} log file".format(self.installer_name))
        click.echo()
        if not os.path.isfile(log_file):
            self.warn("We were unable to locate a log file at {}".format(log_file))
            return None
        return log_file


    def print_lines(self, lines):
        click.echo()
        click.secho("  {} \\".format(lines[0]), bold=True)
        for x in lines[1:-1]:
            click.secho("    {} \\".format(x), bold=True)
        click.secho("    {}".format(lines[-1]), bold=True)


    def _format_line(self, line, honeytail_cmd, log_file):
        return line.format(honeytail_cmd=honeytail_cmd,
                           parser_module=self.parser_module,
                           parser_extra_flags=self.parser_extra_flags,
                           writekey=self.writekey,
                           dataset=self.dataset,
                           log_file=log_file)


    def get_tail_lines(self, log_file):
        honeytail_cmd = os.path.abspath(self.honeytail_loc)

        return map(lambda l: self._format_line(l, honeytail_cmd, log_file), [
            "{honeytail_cmd}",
            """--parser="{parser_module}" {parser_extra_flags}""",
            """--writekey="{writekey}" --dataset="{dataset}" """,
            """--file="{log_file}" """
        ])


    def get_backfill_lines(self, log_file):
        honeytail_cmd = os.path.abspath(self.honeytail_loc)

        return map(lambda l: self._format_line(l, honeytail_cmd, log_file), [
            "{honeytail_cmd}",
            """--parser="{parser_module}" {parser_extra_flags}""",
            "--backfill",
            """--writekey="{writekey}" --dataset="{dataset}" """,
            """--file="{log_file}" """
        ])


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
        click.secho("Backfilling from {log_file} - {estimate}".format(log_file=self.log_file, estimate=estimate))
        subprocess.call(backfill_command, shell=True)
        self.success("Done backfilling from {log_file}".format(log_file=self.log_file))
        click.echo()


    def tail(self, after_backfill):
        """run honeytail and send new events from log.  after_backfill is true if this step was done after calling the backfill() method"""

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

        click.secho("Sending new data from {log_file}".format(log_file=self.log_file))
        subprocess.call(command, shell=True)


    def show_backfill_command(self):
        backfill_lines = self.get_backfill_lines("<LOG_FILE_PATH>")
        click.echo("To backfill from this or other rotated out logs, you can use this command:")
        self.print_lines(backfill_lines)

    def show_tail_command(self):
        tail_lines = self.get_tail_lines(self.log_file)
        click.echo("To tail and send real-time events from {}, run this command:".format(self.log_file))
        self.print_lines(tail_lines)

    def show_commands(self):
        """prints out the commands for backfilling and tailing"""
        self.pre_show_commands_hook()

        self.show_tail_command()
        click.echo()

        self.show_backfill_command()
        click.echo()

        click.echo("NOTE: If you want to backfill and send real-time events from the same file, backfill first.")
        click.echo()


    def fixup_and_suggest(self):
        pass


    def pre_backfill_hook(self):
        pass


    def pre_tail_hook(self, after_backfill):
        pass


    def pre_show_commands_hook(self):
        pass


    def find_log_file(self):
        raise Exception("find_log_file not implemented in subclass")


    def locate_log_file(self):
        self.log_file = self.find_log_file()
        if not self.log_file:
            self.error("We were unable to locate a log file")
        while not self.log_file:
            self.log_file = self.prompt_for_log_file()
        self.success("Using log file at {log_file}".format(log_file=self.log_file))

    def backfill_and_tail(self):
        mode, file_size = self.prompt_for_run_mode()

        if mode == SHOW_COMMANDS:
            self.show_commands()
            return

        click.echo("""
Congratulations! You've set up honeytail to ingest your {installer_name} logs. Try running
a query against your new {installer_name} data:

    https://ui.honeycomb.io/{team_slug}/datasets/{dataset}
""".format(installer_name=self.installer_name, team_slug=self.team_slug, dataset=urllib.quote(self.dataset.lower())))

        if mode == BACKFILL_AND_TAIL:
            self.backfill(file_size)
            self.tail(after_backfill=True)
        elif mode == ONLY_BACKFILL:
            self.backfill(file_size)
            self.show_tail_command()
            click.echo()
        else: # mode == ONLY_TAIL
            self.tail(after_backfill=False)


    def output_step(self, step_number, step_count, step_message):
        click.echo()
        click.secho("[{}/{}] ".format(step_number, step_count), dim=True, nl=False)
        click.secho(step_message + "...", bold=True)

    def start(self):
        click.secho(emoji.emojize(":honeybee: Honeytail {} installer {}".format(self.installer_name, get_version())), bold=True, underline=True)

        steps = [
            ("Checking for honeytail", self.check_honeytail),
            ("Gathering honeycomb account info", self.prompt_for_writekey_and_dataset),
            ("Logging fixes/suggestions", self.fixup_and_suggest),
            ("Locating log file", self.locate_log_file),
            ("Backfilling/tailing", self.backfill_and_tail)
        ]


        for i in xrange(0, len(steps)):
            self.output_step(i+1, len(steps), steps[i][0])
            steps[i][1]()

        click.echo(emoji.emojize(":sparkles: Done."))
