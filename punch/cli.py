#!/usr/bin/env python

from __future__ import print_function, absolute_import, division

import argparse
import sys

import os
import punch
from punch import config as cfr
from punch import file_updater as fu
from punch import replacer as rep
from punch import vcs_configuration as vcsc
from punch import version as ver
from punch import action_register as ar
from punch import helpers as hlp
from punch.vcs_repositories import exceptions as rex
from punch.vcs_repositories import git_flow_repo as gfr
from punch.vcs_repositories import git_repo as gr
from punch.vcs_use_cases import release as ruc


def fatal_error(message, exception=None):
    print(message)
    if exception is not None:
        print("Exception {}: {}".format(
            exception.__class__.__name__,
            str(exception)
        ))
    sys.exit(1)


default_config_file_name = "punch_config.py"

default_config_file_content = """__config_version__ = 1

GLOBALS = {
    'serializer': '{{major}}.{{minor}}.{{patch}}',
}

FILES = []

VERSION = ['major', 'minor', 'patch']

VCS = {
    'name': 'git',
    'commit_message': (
        "Version updated from {{ current_version }}",
        " to {{ new_version }}"),
}
"""

default_version_file_name = "punch_version.py"

default_version_file_content = """major = 0
minor = 1
patch = 0
"""

default_commit_message = \
    "Version update {{ current_version }} -> {{ new_version }}"


def show_version_parts(values):
    for p in values:
        print("{}={}".format(p.name, p.value))


def show_version_updates(version_changes):
    for current, new in version_changes:
        print("  * {} -> {}".format(current, new))


def main(original_args=None):
    parser = argparse.ArgumentParser(
        description="Manages file content with versions."
    )
    parser.add_argument('-c', '--config-file', action='store',
                        help="Config file", default=default_config_file_name)
    parser.add_argument('-v', '--version-file', action='store',
                        help="Version file", default=default_version_file_name)
    parser.add_argument('-p', '--part', action='store')
    parser.add_argument('--set-part', action='store')
    parser.add_argument('-a', '--action', action='store')
    parser.add_argument('--action-options', action='store')
    parser.add_argument('--action-flags', action='store')
    parser.add_argument('--reset-on-set', action='store_true')
    parser.add_argument('--verbose', action='store_true',
                        help="Be verbose")
    parser.add_argument('--version', action='store_true',
                        help="Print the Punch version and project information")
    parser.add_argument(
        '--init',
        action='store_true',
        help="Writes default initialization files" +
             " (does not overwrite existing ones)"
    )
    parser.add_argument(
        '-s',
        '--simulate',
        action='store_true',
        help="Simulates the version increment and" +
             " prints a summary of the relevant data (implies --verbose)"
    )

    args = parser.parse_args()

    # These are here just to avoid "can be not defined" messages by linters
    config = None
    repo = None

    if args.version is True:
        print("Punch version {}".format(punch.__version__))
        print("Copyright (C) 2016 Leonardo Giordani")
        print("This is free software, see the LICENSE file.")
        print("Source: https://github.com/lgiordani/punch")
        print("Documentation: http://punch.readthedocs.io/en/latest/")
        sys.exit(0)

    if args.simulate:
        args.verbose = True

    if args.init is True:
        if not os.path.exists(default_config_file_name):
            with open(default_config_file_name, 'w') as f:
                f.write(default_config_file_content)

        if not os.path.exists(default_version_file_name):
            with open(default_version_file_name, 'w') as f:
                f.write(default_version_file_content)

        sys.exit(0)

    if not any([args.part, args.set_part, args.action]):
        fatal_error("You must specify one of --part, --set-part, or --action")

    if args.set_part and args.reset_on_set:
        set_parts = args.set_part.split(',')
        if len(set_parts) > 1:
            fatal_error(
                "If you specify --reset-on-set you may set only one value"
            )

    if args.verbose:
        print("## Punch version {}".format(punch.__version__))

    try:
        config = cfr.PunchConfig(args.config_file)
    except (cfr.ConfigurationVersionError, ValueError) as exc:
        fatal_error(
            "An error occurred while reading the configuration file.",
            exc
        )

    if not args.simulate:
        if len(config.files) == 0:
            fatal_error("You didn't configure any file")

    current_version = ver.Version.from_file(args.version_file, config.version)
    new_version = current_version.copy()

    if args.part:
        args.action = "punch:increase"
        args.action_options = "part={}".format(args.part)

    if args.set_part:
        args.action = "punch:set"
        args.action_options = args.set_part

    if args.action:
        try:
            action_dict = config.actions[args.action]
        except KeyError:
            print(
                "The requested action {} is not defined.".format(
                    args.action
                )
            )
            sys.exit(0)

        try:
            action_name = action_dict.pop('type')
        except KeyError:
            print("The action configuration is missing the 'type' field.")
            sys.exit(0)

        if args.action_options:
            action_dict.update(hlp.optstr2dict(args.action_options))

        action_class = ar.ActionRegister.get(action_name)
        action = action_class(action_dict)
        new_version = action.process_version(new_version)

    global_replacer = rep.Replacer(config.globals['serializer'])
    current_version_string, new_version_string = \
        global_replacer.run_main_serializer(
            current_version.as_dict(),
            new_version.as_dict()
        )

    if config.vcs is not None:
        special_variables = {
            'current_version': current_version_string,
            'new_version': new_version_string
        }

        vcs_configuration = vcsc.VCSConfiguration.from_dict(
            config.vcs,
            config.globals,
            special_variables
        )
    else:
        vcs_configuration = None

    if args.verbose:
        print("\n* Current version")
        show_version_parts(current_version.values)

        print("\n* New version")
        show_version_parts(new_version.values)

        changes = global_replacer.run_all_serializers(
            current_version.as_dict(),
            new_version.as_dict()
        )

        print("\n* Global version updates")
        show_version_updates(changes)

        print("\nConfigured files")
        for file_configuration in config.files:
            updater = fu.FileUpdater(file_configuration)
            print("* {}:".format(file_configuration.path))
            changes = updater.get_summary(
                current_version.as_dict(),
                new_version.as_dict()
            )
            show_version_updates(changes)

        if vcs_configuration is not None:
            print("\nVersion control configuration")
            print("Name:", vcs_configuration.name)
            print("Commit message", vcs_configuration.commit_message)
            print("Options:", vcs_configuration.options)

    if not args.simulate:
        if vcs_configuration is not None:
            if vcs_configuration.name == 'git':
                repo_class = gr.GitRepo
            elif vcs_configuration.name == 'git-flow':
                repo_class = gfr.GitFlowRepo
            else:
                fatal_error(
                    "The requested version control" +
                    " system {} is not supported.".format(
                        vcs_configuration.name
                    )
                )

            try:
                repo = repo_class(os.getcwd(), vcs_configuration)
            except rex.RepositorySystemError as exc:
                fatal_error(
                    "An error occurred while initializing" +
                    " the version control repository",
                    exc
                )
        else:
            repo = None

        if vcs_configuration is not None:
            # TODO: Create a fake UseCase to allow running this
            # without a repo and outside this nasty if
            uc = ruc.VCSReleaseUseCase(repo)
            uc.pre_start_release()
            uc.start_release()

        for file_configuration in config.files:
            updater = fu.FileUpdater(file_configuration)
            try:
                updater.update(
                    current_version.as_dict(), new_version.as_dict()
                )
            except ValueError as e:
                print("Warning:", e)

        # Write the updated version info to the version file.
        new_version.to_file(args.version_file)

        if vcs_configuration is not None:
            uc.finish_release()
            uc.post_finish_release()
