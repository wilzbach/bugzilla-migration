#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Bugzilla XML File to GitHub Issues Converter
# by Andriy Berestovskyy (https://github.com/semihalf-berestovskyy-andriy/tools/)
# Adapted for the Coq bug tracker migration by Théo Zimmermann
# Adapted for the DLang bug tracker migration by Sebastian Wilzbach
# This script is licensed under the Apache 2.0 license.
#
# How to use the script:
# 1. Generate a GitHub access token:
#    - on GitHub select "Settings"
#    - select "Personal access tokens"
#    - click "Generate new token"
#    - type a token description, i.e. "bugzilla2github"
#    - select "public_repo" to access just public repositories
#    - save the generated token into the migration script
# 2. Export Bugzilla issues into an XML file:
#    - go to
#    https://issues.dlang.org/buglist.cgi?bug_status=UNCONFIRMED&bug_status=NEW&bug_status=ASSIGNED&bug_status=REOPENED&bug_status=RESOLVED&bug_status=VERIFIED&bug_status=CLOSED&limit=0&order=bug_id&query_format=advanced&resolution=---&resolution=FIXED&resolution=INVALID&resolution=WONTFIX&resolution=DUPLICATE&resolution=WORKSFORME&resolution=MOVED&component=tools
#    - at the very end click the XML icon
#    - save the XML into a file: tools.xml
# 3. Run the migration script and check all the warnings:
#    bugzilla2github -x tools.xml -o berestovskyy -r test -t beefbeefbeef
# 4. Run the migration script again and force the updates:
#    bugzilla2github -x tools.xml -o berestovskyy -r test -t beefbeefbeef -f

import csv, getopt, json, os, pprint, re, requests, sys, time, xml.etree.ElementTree

# Existing issues means issue numbers already taken on GitHub (by PRs mostly).
# The script can find these by itself but this will spare API requests.
force_update = False
xml_file = "bugzilla.xml"
github_url = "https://api.github.com"
github_owner = ""
github_repo = ""
github_token = ""

email2login = {
    "__name__": "email to GitHub login",
    "greensunny12@gmail.com": "wilzbach",
    "greeenify@gmail.com": "wilzbach",
    "andrei@erdani.com": "andralex",
}
status2state = {
    "__name__": "status to GitHub state",
    "NEW": False,
    "UNCONFIRMED": False,
    "CONFIRMED": False,
    "VERIFIED": False,
    "ASSIGNED": False,
    "IN_PROGRESS": False,
    "RESOLVED": True,
    "CLOSED": True,
    "REOPENED": False,
}
keywords2labels = {
    "__name__": "keywords to GitHub labels",
    "compatibility": ["kind: compatibility"],
    "performance": ["kind: performance"],
    "performance, regression":  ["kind: performance", "kind: regression"],
    "regression": ["kind: regression"],
    "dll": ["kind: dll"],
    "pull": ["kind: has pull"],
    "accepts-invalid": ["kind: accepts-invalid"],
    "patch": ["kind: patch"],
    "link-failure": ["kind: link-failure"],
    "wrong-code": ["kind: wrong-code"],
    "Optlink": ["kind: Optlink"],
    "mangling": ["kind: mangling"],
    "ice": ["kind: ice"],
    "ice-on-valid-code": ["kind: ice"],
    "bootcamp": ["kind: bootcamp"],
    "rejects-valid": ["kind: rejects-valid"],
    "industry": ["kind: industry"],
    "symdeb": ["kind: symdeb"],
    "SIMD": ["kind: SIMD"],
    "preapproved": ["kind: preapproved"],
    "diagnostic": ["kind: diagnostic"],
}
resolution2labels = {
    "__name__": "resolution to GitHub labels",
    "FIXED": [],
    "DUPLICATE": ["resolved: duplicate"],
    "INVALID": ["resolved: invalid"],
    "MOVED": ["resolved: moved"],
    "WONTFIX": ["resolved: won't fix"],
    "WORKSFORME": ["resolved: works for me"],
}
op_sys2labels = {
    "__name__": "Operating System to GitHub labels",
    "Mac OS X": [ "platform: macOS" ],
    "Windows": [ "platform: Windows" ],
    "FreeBSD": [ "platform: FreeBSD" ],
    "Linux": [],
    "Other": [],
    "All": []
}
bug_unused_fields = [
    "actual_time",
    "assigned_to.name",
    "attachment.isobsolete",
    "attachment.ispatch",
    "attachment.isprivate",
    "bug_file_loc",
    "cclist_accessible",
    "classification",
    "classification_id",
    "comment_sort_order",
    "component",
    "deadline",
    "delta_ts",
    "estimated_time",
    "everconfirmed",
    "long_desc.isprivate",
    "priority",
    "product",
    "remaining_time",
    "reporter_accessible",
    "rep_platform",
    "bug_severity",
    "target_milestone",
    "token",
]
comment_unused_fields = [
    "comment_count",
    "attachid",
    "work_time",
]
attachment_unused_fields = [
    "attacher",
    "attacher.name",
    "date",
    "delta_ts",
    "token",
]

def usage():
    print("Bugzilla XML file to GitHub Issues Converter")
    print("Usage: %s [-h] [-f]\n" \
        "\t[-x <src XML file>]\n" \
        "\t[-o <dst GitHub owner>] [-r <dst repo>] [-t <dst access token>]\n" \
            % os.path.basename(__file__))
    print("Example:")
    print("\t%s -h" % os.path.basename(__file__))
    print("\t%s -x bugzilla.xml -o dst_login -r dst_repo -t dst_token" \
            % os.path.basename(__file__))
    exit(1)


def XML2dict(parent):
    ret = {}

    for key in parent:
        # TODO: debug
        # print len(key), key.tag, key.attrib, key.text
        if len(key) > 0:
            val = XML2dict(key)
        else:
            val = key.text
        if key.text:
            if key.tag not in ret:
                ret[key.tag] = val
            else:
                if isinstance(ret[key.tag], list):
                    ret[key.tag].append(val)
                else:
                    ret[key.tag] = [ret[key.tag], val]
        # Parse attributes
        for name, val in list(key.items()):
            ret["%s.%s" % (key.tag, name)] = val

    return ret


def str2list(map, str):
    if str not in map:
        print("WARNING: unable to convert %s: %s" % (map["__name__"], str))
        # Suppress further reports
        map[str] = []

    return map[str]


def str2str(map, str):
    if str not in map:
        print("WARNING: unable to convert %s: %s" % (map["__name__"], str))
        # Suppress further reports
        map[str] = None

    return map[str]


def id_convert(id):
    global github_owner, github_repo
    return "[BZ#" + id + "](https://github.com/" + github_owner + "/" + github_repo + "/issues?q=is%3Aissue%20%22Original%20bug%20ID%3A%20BZ%23" + id + "%22)"

def id_convert_from_match(match):
    return re.sub(r'\#', "", match.group(1)) + id_convert(match.group(2))

def ids_convert(ids):
    ret = []

    if not ids:
        return ""
    if isinstance(ids, list):
        for id in ids:
            ret.append(id_convert(id))
    else:
        ret.append(id_convert(ids))

    return ", ".join(ret)

def see_also_convert(see_also):
    result = re.search('id=(\d+)$', see_also)
    if not result:
        return see_also
    else:
        return id_convert(result.group(1))


def email_convert(email, name):
    ret = str2str(email2login, email)
    if ret:
        return "@" + ret
    else:
        if name and not name.find("@") >= 0:
            return "%s &lt;<%s>&gt;" % (name, email)
        else:
            return email


def emails_convert(emails):
    ret = []
    if isinstance(emails, list):
        for email in emails:
            if email != "github-bugzilla@puremagic.com":
                ret.append(email_convert(email, None))
    else:
        ret.append(email_convert(emails, None))

    return ret


def fields_ignore(obj, fields):
    # Ignore some Bugzilla fields
    for field in fields:
        obj.pop(field, None)

def fields_dump(obj):
    # Make sure we have converted all the fields
    for key, val in list(obj.items()):
        print(" " * 8 + "%s[%d] = %s" % (key, len(val), val))


def attachment_convert(idx, attach):
    ret = []

    id = attach.pop("attachid")
    ret.append("> Attached file: [%s](https://issues.dlang.org/bugfiles/attachment.cgi?id=%s) (%s, %s bytes)" % (attach.pop("filename"), id, attach.pop("type"), attach.pop("size")))
    if "desc" in attach:
        ret.append("> Description:   " + attach.pop("desc"))

    # Ignore some fields
    global attachment_unused_fields
    fields_ignore(attach, attachment_unused_fields)
    # Make sure we have converted all the fields
    if attach:
        print("WARNING: unconverted attachment fields:")
        fields_dump(attach)

    idx[id] = "\n".join(ret)


def attachments_convert(attachments):
    ret = {}
    if isinstance(attachments, list):
        for attachment in attachments:
            attachment_convert(ret, attachment)
    else:
        attachment_convert(ret, attachments)

    return ret


def date_convert(date):
    result = re.match(r'(\d\d\d\d-\d\d-\d\d) (\d\d:\d\d:\d\d) \+(\d\d)(\d\d)', date)
    if not result:
        print(("Date %s was not converted!" % date))
        exit(1)
    return "{a}T{b}+{c}:{d}".format(a = result.group(1), b = result.group(2),
                                    c = result.group(3), d = result.group(4))


def comment_convert(comment, attachments):
    ret = []

    id = int(comment.pop("commentid"))

    ret.append("Comment author: " + email_convert(comment.pop("who"), comment.pop("who.name", None)))
    ret.append("")
    ret.append(comment.pop("thetext", "*No description provided.*").replace("@", "@ "))
    ret.append("")
    # Convert attachments if any
    if "attachid" in comment:
        attachid = comment.pop("attachid")
        if attachid in attachments:
            ret.append(attachments.pop(attachid))
            ret.append("")
    ret.append("")

    # Syntax: convert "bug id" to "BZ#id"
    for i, val in enumerate(ret):
        val = re.sub(r"\(In reply to comment \#\d+\)","", val)
        ret[i] = re.sub(r"(?i)(bug(?:\s+report)?\s+|feature wish\s+|\s\#)(\d\d?\d?\d?)", id_convert_from_match, val)

    created_at = date_convert(comment.pop("bug_when"))

    # Ignore some comment fields
    global comment_unused_fields
    fields_ignore(comment, comment_unused_fields)
    # Make sure we have converted all the fields
    if comment:
        print("WARNING: unconverted comment fields:")
        fields_dump(comment)

    return { "body": "\n".join(ret), "created_at": created_at }


def comments_convert(comments, attachments):
    ret = []
    if isinstance(comments, list):
        for comment in comments:
            ret.append(comment_convert(comment, attachments))
    else:
        ret.append(comment_convert(comments, attachments))

    return ret


def bug_convert(bug):
    ret = {}
    ret["body"] = []
    ret["body"].append("Note: the issue was created automatically migrated from https://issues.dlang.org")
    ret["body"].append("")
    ret["labels"] = []
    ret["comments"] = []
    attachments = {}

    # Convert bug_id to number
    ret["number"] = int(bug.pop("bug_id"))
    # Convert attachments if any
    if "attachment" in bug:
        attachments = attachments_convert(bug.pop("attachment"))
    # Convert long_desc and attachment to comments
    ret["comments"].extend(comments_convert(bug.pop("long_desc"), attachments))
    # Convert short_desc to title
    ret["title"] = bug.pop("short_desc")
    # Convert creation_ts to created_at
    ret["created_at"] = date_convert(bug.pop("creation_ts"))
    # Convert component to labels
    # ret["labels"].extend(str2list(component2labels, bug.pop("component")))
    # Convert bug_status to state
    ret["closed"] = str2str(status2state, bug.pop("bug_status"))
    # We only assign open bug reports
    assignee = str2str(email2login, bug.pop("assigned_to"))
    if not ret["closed"] and assignee:
        ret["assignee"] = assignee
    # Approximate closing date with last update date
    updated_at = bug.pop("delta_ts")
    if ret["closed"]:
        ret["closed_at"] = date_convert(updated_at)
    # Convert (optional) keywords to labels
    for keyword in bug.pop("keywords","").split(","):
        ret["labels"].extend(str2list(keywords2labels, keyword.strip()))
    # Convert resolution to labels
    if "resolution" in bug:
        ret["labels"].extend(str2list(resolution2labels, bug.pop("resolution")))
    # Convert op_sys to labels
    if "op_sys" in bug:
        ret["labels"].extend(str2list(op_sys2labels, bug.pop("op_sys")))

    # Create the bug description
    ret["body"].append("Original bug ID: BZ#%d" % ret["number"])
    ret["body"].append("From: " + email_convert(bug.pop("reporter"),
                        bug.pop("reporter.name", None)))
    ret["body"].append("Reported version: " + bug.pop("version"))
    if "cc" in bug:
        ret["body"].append("CC:   " + ", ".join(emails_convert(bug.pop("cc"))))
    # Extra information
    ret["body"].append("")
    if "dup_id" in bug:
        ret["body"].append("Duplicates:   " + ids_convert(bug.pop("dup_id")))
    if "dependson" in bug:
        ret["body"].append("Depends on:   " + ids_convert(bug.pop("dependson")))
    if "blocked" in bug:
        ret["body"].append("Blocker for:  " + ids_convert(bug.pop("blocked")))
    if "see_also" in bug:
        see_also = bug.pop("see_also")
        if isinstance(see_also, str):
            ret["body"].append("See also: " + see_also_convert(see_also))
        else:
            for item in see_also:
                ret["body"].append("See also: " + see_also_convert(item))
    ret["body"].append("")

    # Put everything together
    ret["body"] = "\n".join(ret["body"])

    # Ignore some bug fields
    global bug_unused_fields
    fields_ignore(bug, bug_unused_fields)
    # Make sure we have converted all the fields
    if bug:
        print("WARNING: unconverted bug fields:")
        fields_dump(bug)

    # Make sure we have converted all the attachments
    if attachments:
        print("WARNING: unconverted attachments:")
        fields_dump(attachments)

    return ret


def bugs_convert(xml_root):
    issues = {}
    for xml_bug in xml_root.iter("bug"):
        bug = XML2dict(xml_bug)
        issue = bug_convert(bug)
        # Check for duplicates
        id = issue.pop("number")
        if id in issues:
            print(("Error checking for duplicates: bug #%d is duplicated in the '%s'"
                            % (id, xml_file)))
        issues[id] = issue

    return issues


def github_get(url, avs = {}):
    global xml_file, github_url, github_owner, github_repo, github_token

    if url[0] == "/":
        u = "%s%s" % (github_url, url)
    elif url.startswith("https://"):
        u = url
    elif url.startswith("http://"):
        u = url
    else:
        u = "%s/repos/%s/%s/%s" % (github_url, github_owner, github_repo, url)

    # TODO: debug
    # print "GET: " + u

    avs["access_token"] = github_token
    return requests.get(u, params = avs)


def github_post(url, avs = {}, fields = []):
    global force_update
    global xml_file, github_url, github_owner, github_repo, github_token

    if url[0] == "/":
        u = "%s%s" % (github_url, url)
    else:
        u = "%s/repos/%s/%s/%s" % (github_url, github_owner, github_repo, url)

    d = {}
    # Copy fields into the data
    for field in fields:
        if field not in avs:
            print("Error posting filed %s to %s" % (field, url))
            exit(1)
        d[field] = avs[field]

    # TODO: debug
    # print "POST: " + u
    # print "DATA: " + json.dumps(d)

    if force_update:
        return requests.post(u, params = { "access_token": github_token },
                                data = json.dumps(d))
    else:
        if not github_post.warn:
            print("Skipping POST... (use -f to force updates)")
            github_post.warn = True
        return True

github_post.warn = False


def github_label_create(label):
    if not github_get("labels/" + label):
        print("\tcreating label '%s' on GitHub..." % label)
        r = github_post("labels", {
            "name": label,
            "color": "0"*6,
        }, ["name", "color"])
        if not r:
            print("Error creating label %s: %s" % (label, r.headers))
            exit(1)


def github_labels_check(issues):
    global force_update

    labels_set = set()
    for id in issues:
        for label in issues[id]["labels"]:
            labels_set.add(label)

    for label in labels_set:
        if github_get("labels/" + label):
            print("\tlabel '%s' exists on GitHub" % label)
        else:
            if force_update:
                github_label_create(label)
            else:
                print("WARNING: label '%s' does not exist on GitHub" % label)


def github_assignees_check(issues):
    a_set = set()
    for id in issues:
        if "assignee" in issues[id]:
            a_set.add(issues[id]["assignee"])

    for assignee in a_set:
        if not github_get("/users/" + assignee):
            print("Error checking user '%s' on GitHub" % assignee)
            exit(1)
        else:
            print("Assignee '%s' exists" % assignee)


def github_issue_exist(number):
    if github_get("issues/%d" % number):
        return True
    else:
        return False


def github_issue_get(number):
    req = github_get("issues/%d" % number)
    if not req:
        print("Error getting GitHub issue #%d: %s" % (number, req.headers))
        exit(1)

    return req.json()


def github_issue_append(bugzilla_id, issue):
    global github_owner, github_repo, github_token
    params = { "access_token": github_token }
    headers = { "Accept": "application/vnd.github.golden-comet-preview+json" }
    print("\timporting BZ#%d on GitHub..." % bugzilla_id)
    u = "https://api.github.com/repos/%s/%s/import/issues" % (github_owner, github_repo)
    comments = issue.pop("comments", [])
    # We can't assign people which are not in the organization / collaborators on the repo
    # if github_owner != "dlang":
        # issue.pop("assignee", None)
    r = requests.post(u, params = params, headers = headers,
                      data = json.dumps({ "issue": issue, "comments": comments }))
    if not r:
        print("Error importing issue on GitHub:\n%s" % r.text)
        print("For the record, here was the request:\n%s" % json.dumps({ "issue": issue, "comments": comments }))
        exit(1)
    u = r.json()["url"]
    wait = 1
    r = False
    while not r or r.json()["status"] == "pending":
        time.sleep(wait)
        wait = 2 * wait
        r = requests.get(u, params = params, headers = headers)
    if not r.json()["status"] == "imported":
        print("Error importing issue on GitHub:\n%s" % r.text)
        exit(1)
    # The issue_url field of the answer should be of the form .../ISSUE_NUMBER
    # So it's easy to get the issue number, to check that it is what was expected
    result = re.match("https://api.github.com/repos/" + github_owner + "/" + github_repo + "/issues/(\d+)", r.json()["issue_url"])
    if not result:
        print("Error while parsing issue number:\n%s" % r.text)
    issue_number = result.group(1)
    with open("bugzilla2github.log", "a") as f:
        f.write("%d, %s\n" % (bugzilla_id, issue_number))
    return issue_number


def github_issues_add(issues):
    sorted_issues = sorted(issues.items(), key=lambda a: a[0])
    for bugzilla_id, issue in sorted_issues:
        # if force_update:
        print("Creating issue #%d..." % bugzilla_id)
        github_issue_append(bugzilla_id, issue)
        # break


def args_parse(argv):
    global force_update
    global xml_file, github_owner, github_repo, github_token

    try:
        opts, args = getopt.getopt(argv,"hfo:r:t:x:")
    except getopt.GetoptError:
        usage()
    for opt, arg in opts:
        if opt == '-h':
            usage()
        elif opt == "-f":
            print("WARNING: the repo will be UPDATED! No backups, no undos!")
            print("Press Ctrl+C within next 5 seconds to cancel the update:")
            time.sleep(5)
            force_update = True
        elif opt == "-o":
            github_owner = arg
        elif opt == "-r":
            github_repo = arg
        elif opt == "-t":
            github_token = arg
        elif opt == "-x":
            xml_file = arg

    # Check the arguments
    # if (not xml_file or not github_owner or not github_repo or not github_token):
        # print("Error parsing arguments: "
                # "please specify XML file, GitHub owner, repo and token")
        # usage()


def main(argv):
    global xml_file, github_owner, github_repo, existingIssues

    # Parse command line arguments
    args_parse(argv)
    print("===> Converting Bugzilla reports to GitHub Issues...")
    print("\tSource XML file:    %s" % xml_file)
    print("\tDest. GitHub owner: %s" % github_owner)
    print("\tDest. GitHub repo:  %s" % github_repo)

    xml_tree = xml.etree.ElementTree.parse(xml_file)
    xml_root = xml_tree.getroot()
    issues = bugs_convert(xml_root)

    try:
        with open("bugzilla2github.log", "r") as f:
            print("===> Skipping already imported issues (WARNING: this shouldn't happen when you run this script for the first time)...")
            # time.sleep(5)
            imported_bugs = csv.reader(f)
            for imported_bug in imported_bugs:
                issues.pop(int(imported_bug[0]), None)
    except IOError:
        print("===> No log file found. Not skipping any issue.")

    # print("===> Checking last existing issue actually exists.")
    # if not github_issue_exist(existingIssues):
        # print("Last existing issue doesn't actually exist. Aborting!")
        # exit(1)
    # print("===> Checking whether the following issue was created but not saved.")
    # github_issue = github_get("issues/%d" % (existingIssues + 1))
    # if github_issue:
        # result = re.search("Original bug ID: BZ#(\d+)", github_issue.json()["body"])
        # if result:
            # print("Indeed, this was the case.")
            # bugzilla_id = int(result.group(1))
            # issues.pop(bugzilla_id, None)
            # with open("bugzilla2github.log", "a") as f:
                # f.write("%d, %d\n" % (bugzilla_id, existingIssues + 1))

    # TODO: re-enable
    # print("===> Checking all the labels exist on GitHub...")
    # github_labels_check(issues)
    # print("===> Checking all the assignees exist on GitHub...")
    # github_assignees_check(issues)

    # fake_issue = { "title": "Fake issue", "body": "Fake issue", "closed": True }
    # for i in xrange(1,existingIssues + 1):
    #     github_issue_append(0, fake_issue)
 
    # vals = list(sorted(issues.items(), key=lambda a: a[0]))
    # print(len(vals))
    # print(json.dumps(vals[0][1], indent=4))
    # return

    # issues_filtered = {17044: issues[17044]}

    # print("===> Adding Bugzilla reports on GitHub...")
    github_issues_add(issues)


if __name__ == "__main__":
    main(sys.argv[1:])
