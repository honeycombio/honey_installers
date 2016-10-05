#!/usr/bin/env python

import subprocess
from nginxparser import NginxParser

import click
import os.path
import pprint
import semver
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.basename(__file__), "..")))

from honey_installer import (HoneyInstaller, bold, get_version)

INSTALLER_NAME = "nginx"
INSTALLER_VERSION = get_version() + "-" + platform.system().lower()
PARSER_MODULE = "nginx"
DEFAULT_DATASET = "Nginx"

MESSAGES = {
    # variable: (version, description),
    "$bytes_sent": ("1.0.0", "The size of the response sent back to the client, including headers."),
    "$host": ("1.0.0", "The requested Host header, identifying how your server was addressed."),
    "$http_authorization": ("1.0.0", "Logging authorization headers can help associate logs with individual users."),
    "$remote_addr": ("1.0.0", "This field holds the IP address of the host making the connection to nginx."),
    "$remote_user": ("1.0.0", "The user name supplied when using basic authentication."),
    "$http_x_forwarded_for": ("1.0.0", "When running behind a load balancer, this header will hold the origin IP address."),
    "$http_x_forwarded_proto": ("1.0.0", "If you're terminating TLS in front of nginx, this header will hold the origin protocol"),
    "$http_referer": ("1.0.0", "The referring site, when the client followed a link to your site."),
    "$http_user_agent": ("1.0.0", "The User-Agent header, which is useful in identifying your clients."),
    "$request": ("1.0.0", "The HTTP verb, request path, and protocol version."),
    "$status": ("1.0.0", "The HTTP status code returned for this request."),
    "$request_time": ("1.0.0", "The time, in milliseconds, your server took to respond to the request."),
    "$request_length": ("1.0.0", "This is the length of the client's request to you, including headers and body."),
    "$server_name": ("1.0.0", "This is the hostname of the machine that accepted the request."),
    "$request_id": ("1.11.0", "add a unique ID to every request."),
}

NGINX_WHITELIST_LOCATIONS = [
    "/etc/nginx/nginx.conf",            # ubuntu, apt default
    "/opt/local/nginx/nginx.conf",      # was on one of my servers somewhere.
    "/opt/nginx/conf/nginx.conf",
    "/usr/local/nginx/nginx.conf",      # was on one of my servers somewhere.
    "/usr/local/etc/nginx/nginx.conf",  # OSX, Homebrew
]

def _find_nginx_conf(conf_loc=None):
    click.echo("Looking for nginx config...")
    click.echo()

    found = False
    if not conf_loc:
        for conf_loc in NGINX_WHITELIST_LOCATIONS:
            if os.path.isfile(conf_loc):
                click.echo("Found nginx config at %s" % conf_loc)
                click.echo()
                found = True
                break

    if os.path.isfile(conf_loc):
        found = True

    while not found:
        click.echo("We couldn't locate your nginx config. Could you please type the full location below?\n")
        # Should we log these on the server to add to the whitelist?
        conf_loc = click.prompt("Nginx Conf Location: ")
        if os.path.isfile(conf_loc):
            found = True
            break
        if conf_loc.lower in ["", "exit", "quit", "q"]:
            click.echo("Exiting.\n")
            sys.exit(1)

    return conf_loc


def _parse_nginx(conf_loc, debug):
    click.echo("Processing Nginx config: %s" % conf_loc)
    try:
        with open(conf_loc, "r") as fh:
            parsed = NginxParser(fh.read()).as_list()
    except OSError:
        click.echo("We can't read your nginx config with the existing permissions. Please update the permissions or run as sudo and try again.")
        sys.exit(1)

    def _descend(parsed):
        found_formats = []
        found_logs = []
        for item in parsed:
            if isinstance(item, list):
                x, y = _descend(item)
                found_formats.extend(x)
                found_logs.extend(y)

            if isinstance(item, str):
                if item == "log_format":
                    found_formats.append(parsed)
                if item == "access_log":
                    found_logs.append(parsed)
        return found_formats, found_logs

    log_formats, access_logs = _descend(parsed)

    if debug:
        click.echo("log formats and access logs found:")
        pprint.pprint(log_formats)
        pprint.pprint(access_logs)

    return access_logs, log_formats


def _get_access_log(access_logs, conf_loc, log_filename=None, log_format_name=None):
    abs_base_path = __file__

    if not log_filename:
        if access_logs:
            abs_base_path = conf_loc
            if len(access_logs) > 1:
                click.echo("\nWe found the following access logs in your nginx config:")
                for i, log_list in enumerate(access_logs):
                    click.echo(" [%s] %s" % ((i + 1), log_list[1].split()[0]))
                log_index = click.prompt("Which log would you like to send to honeycomb?", type=int, default=1)
                try:
                    log_item = access_logs[log_index - 1]
                except (TypeError, IndexError):
                    click.echo("That wasn't one of the choices. You can also specify a full access log location with the --file option. Sorry.")
                    sys.exit()
            else:
                log_item = access_logs[0]
                click.echo("Using log located at:")
                click.echo("    %s" % log_item[1])

            log_parts = log_item[1].split()
            log_filename = log_parts[0]
            if len(log_parts) > 1:
                log_format_name = log_parts[1]
            else:
                log_format_name = "combined"
        else:
            log_filename = "/var/log/nginx/access.log"
            click.echo("We'll start by using the default log location of")
            click.echo("    %s" % log_filename)

    # Turn a relative path into an absolute path
    if log_filename[0] != "/":
        log_filename = os.path.join(os.path.dirname(abs_base_path), log_filename)

    if not log_format_name:
        if access_logs:
            # Attempt to guess the log format.
            for i, log_list in enumerate(access_logs):
                parts = log_list[1].split()
                if parts[0] == log_filename and len(parts) > 1:
                    log_format_name = parts[1]

            # Still no? show the found formats and ask
            if not log_format_name:
                click.echo("\nWe found the following access logs in your nginx config:")
                formats = set()
                for log_list in access_logs:
                    parts = log_list[1].split()
                    if len(parts) > 1:
                        formats.add(parts[1])
                        click.echo("[%s] %s" % (parts[1], parts[0]))
                if formats:
                    log_format_name = click.prompt("Which log format would you like to use?", default=list(formats)[0], type=click.Choice(formats))

    if not log_format_name:
        log_format_name = "combined"
        click.echo("and the default log format '%s'" % log_format_name)

    click.echo("Checking Permissions...")
    try:
        with open(log_filename) as fb:
            # check that we can read a line; doesn't matter what it is.
            _ = fb.readline()
    except IOError:
        click.echo("It doesn't look like we have permissions to read that file.\n")
        click.echo("Please change the permissions or run as sudo and try again.")
        sys.exit()

    return log_filename, log_format_name


def _give_log_recs(conf_loc, name, log_filename, all_formats, nginx_version):
    click.echo("-" * 80)

    full_format = None
    for f in all_formats:
        if f[1].startswith(name + " "):
            full_format = f[1]
            break

    if not full_format:
        click.echo("Something went wrong and I can't identify your format.")
        click.echo("The program is exiting and we're really unhappy.")
        sys.exit(1)

    click.echo("""
Honeycomb works best with lots of fields, and nginx has a great set of
extra fields available out of the box. Let's take a look at your config to see
if you're missing anything useful.

We'll return a list of variables to add to your log_format line.
""")
    click.echo("For reference, your current format is:")
    click.echo("    {}".format(full_format))
    click.echo()
    if not click.confirm("Ready to see what you're missing?", default="Y"):
        click.echo("Ok, aborting.")
        sys.exit(0)

    click.echo("-" * 80)
    click.echo("Your access log is missing the following useful fields:")

    passed_all_checks = True

    vars_to_add = list()

    for var, ver_mess in MESSAGES.iteritems():
        version, message = ver_mess
        if var not in full_format and semver.compare(nginx_version, version) >= 0:
            click.secho("    {:<24}".format(var), bold=True, nl=False)
            click.echo(": {}".format(message))
            vars_to_add.append(var)
            passed_all_checks = False
    click.echo("-" * 80)
    click.secho("Review complete.", bold=True)
    click.echo("""
Here's a complete log format that we would use for nginx with Honeycomb:

    log_format   {full_format} {vars_to_add}';
    access_log   {log_filename}  {name};
""".format(
    name = name,
    full_format = full_format.rstrip("' "),
    vars_to_add = " ".join(vars_to_add),
    log_filename = log_filename))

    click.echo("If you like these changes, go ahead and edit your nginx config (at {}) now.".format(conf_loc))
    click.echo("Please make sure to reload nginx (sudo nginx -s reload) after any changes to the config.")
    if not click.confirm("""\nOnce you're finished making changes and have reloaded nginx,
hit Enter to continue, 'n' to abort""", default=True):
        click.echo("Ok, aborting.")
        sys.exit(0)

def _backfill(honeytail_loc, conf_loc, log_name, log_format, dataset, writekey):
    click.echo("""
--
honeytail only parses new log lines by default (like `tail -f`) but it can also
backfill existing logs.

Backfilling a little data from the existing log will get you started with the
query tools faster.
""")
    ask = click.confirm("Would you like to backfill using {} now?".format(log_name),
        default=True)

    honeytail_cmd = os.path.abspath(honeytail_loc)
    backfill_command = """{honeytail_cmd} --writekey="{writekey}" --parser="nginx" --nginx.conf="{conf_loc}" --nginx.format="{log_format}" --file="{log_name}" --tail.read_from=beginning --tail.stop --dataset="{dataset}" --backoff""".format(
        honeytail_cmd=honeytail_cmd,
        conf_loc=conf_loc,
        log_format=log_format,
        log_name=log_name,
        dataset=dataset,
        writekey=writekey,
    )

    if self.debug:
        backfill_command += " --debug"

    commands = backfill_command.split()
    if ask:
        click.echo("About to run (this could take a few minutes):")
        click.echo()
        click.echo("  {} \\".format(commands[0]))
        for x in commands[1:-1]:
            click.echo("    {} \\".format(x))
        click.echo("    {}".format(commands[-1]))
        subprocess.call(backfill_command, shell=True)
        click.echo("All done.")
    else:
        ## Print command
        click.echo("""
OK, we're not going to backfill right now. In order to backfill later, you should
snag a copy of the nginx config to preserve the current log config.
Please make a copy:
    cp {} ~/

When you're ready to backfill, use the following command
""".format(conf_loc))
        click.echo()
        click.echo("  {} \\".format(commands[0]))
        for x in commands[1:-1]:
            if "nginx.conf" in x:
                x = x.replace(conf_loc, "~/{}".format(os.path.basename(conf_loc)))
            click.echo("    {} \\".format(x))
        click.echo("    {}".format(commands[-1]))
        if not click.confirm("All copied and ready to continue?", default=True):
            click.echo("Ok, aborting.")
            sys.exit()

def _get_nginx_version():
    '''calls out to nginx -v to get the nginx version number (eg 1.4.2)'''
    verstring = subprocess.check_output(["nginx", "-v"], stderr=subprocess.STDOUT)
    version = verstring.split()[2]
    vernum = version.split("/")[1]
    return vernum

class NginxInstaller(HoneyInstaller):
    def __init__(self, writekey, dataset, honeytail, debug, log_filename, nginx_conf, log_format):
        super(NginxInstaller, self).__init__(INSTALLER_NAME, INSTALLER_VERSION, PARSER_MODULE,
                                             "", # we'll fill this in in pre_*_hook below
                                             writekey, dataset, DEFAULT_DATASET, honeytail, debug)
        self.log_filename = log_filename
        self.nginx_conf = nginx_conf
        self.log_format = log_format
        self.parser_extra_flags_format = """--nginx.conf="{nginx_conf}" --nginx.format="{log_format}" """

    def hook(self):
        pass

    def pre_backfill_hook(self):
        self.parser_extra_flags = self.parser_extra_flags_format.format(nginx_conf=self.nginx_conf, log_format=self.log_format)
        
    def pre_tail_hook(self, after_backfill):
        self.parser_extra_flags = self.parser_extra_flags_format.format(nginx_conf=self.nginx_conf, log_format=self.log_format)

        if not after_backfill:
            click.echo("""
In order to backfill later, you should
snag a copy of the nginx config to preserve the current log config.
Please make a copy:
    cp {} ~/

When you're ready to backfill, use the following command""".format(self.nginx_conf))
        home_conf = "~/{}".format(os.path.basename(self.nginx_conf))
        self.print_lines(map(lambda line: line.replace(self.nginx_conf, home_conf), self.get_backfill_lines()))
        click.echo()
        
    def pre_show_commands_hook(self):
        self.parser_extra_flags = self.parser_extra_flags_format.format(nginx_conf=self.nginx_conf, log_format=self.log_format)
        
    def find_log_file(self):
        conf_loc = _find_nginx_conf(self.nginx_conf)

        found_logs, log_formats = _parse_nginx(conf_loc, self.debug)

        # We can largely assume good formatting, if nginx accepts it, we should be in good shape.
        if self.log_filename and self.log_format:
            access_log_format = self.log_format
            access_log_name = self.log_filename
        else:
            access_log_name, access_log_format = _get_access_log(found_logs, conf_loc, self.log_filename, self.log_format)
        
        ## Check the log_format and give recommendations
        nginx_version = _get_nginx_version()
        if not log_formats:
            log_formats.append(["log_format", 'combined \'$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"\''])
            click.echo("It looks like you're using the default nginx configuration.")
            click.echo("""The defaults are great, but your logs will be even more powerful with more data!
We'll show you how, after you get a chance to backfill any existing logs.""")

        _give_log_recs(conf_loc, access_log_format, access_log_name, log_formats, nginx_version)

        # ugly side effects here
        self.log_format = access_log_format
        self.nginx_conf = conf_loc
        
        click.echo()
        
        return access_log_name

@click.command()
@click.option("--writekey", "-k", help="Your Honeycomb Writekey", default="")
@click.option("--dataset", "-d", help="Your Honeycomb Dataset", default=DEFAULT_DATASET)
@click.option("--file", "-f", "log_filename", help="Nginx Access Log File")
@click.option("--nginx.conf", "nginx_conf", help="Nginx Config location")
@click.option("--nginx.format", "nginx_format", help="The name of the log_format from your nginx config that you wish to use with Honeycomb")
@click.option("--honeytail", help="Honeytail location", default="honeytail")
@click.option("--debug", help="Turn Debug mode on", default=False)
@click.version_option(INSTALLER_VERSION)
def start(writekey, dataset, log_filename, nginx_conf, nginx_format, honeytail, debug):

    installer = NginxInstaller(writekey, dataset, honeytail, log_filename, debug, nginx_conf, nginx_format)
    installer.start()


if __name__ == "__main__":
    start()
