If you are interested in using ambient, the built-in workflow is focused on triaging GH issues, so it needs a little tweaking through the prompts to work with Jira.


**If you run into issues reach out to [\#wg-ai-bug-bash-march-2026](https://redhat.enterprise.slack.com/archives/C0AM0Q17NAX).**

**NOTE:  Ambient will add the below labels to the jiras.  If not using Ambient you must label your issues\!**

**Getting started with Ambient:**

* Open ambient ([https://](https://ambient-code.apps.rosa.vteam-uat.0ksl.p3.openshiftapps.com)red.ht/ambient)  
* Follow the User Guide to get your first session running: [Ambient Code Platform (ACP) User Guide](https://docs.google.com/document/d/1Mg7Z9mvLNGliNu5HVxmWH4wTcqYC4rOxK8GJ4oDXm_g/edit?tab=t.sgef50g04n6s)  
* Make sure the integrations are set up \- following step \#3 in the user guide or the [Ambient-integrations-walkthrough.pdf](https://drive.google.com/file/u/1/d/1OVAQ8Oe6Qwg8HWenJwTk6BbLNAG4fHbR/view?usp=sharing) document here (also linked in the user guide).  
* Create your first session using the Triage Workflow  
* Recommendation \- set your model to “Opus 4.6”  
* Load your component’s repo(s) in via context in Ambient \- do this before prompting  
* Add [`https://github.com/opendatahub-io/architecture-context`](https://github.com/opendatahub-io/architecture-context) as context also \- this can improve results for teams that are part of RHOAI **OR** you can use what is added to the [prompt below](#bookmark=id.11y3sj21mg3e).

**Once you have an AI session (Ambient/Claude/Cursor) connected to jira:**

* Prompt adjusting the jql as needed (recommendation: limit query to \~20 issues at a time):

---

`using my jira connection - triage the issues that are from this jql ‘project = RHOAIENG and status in (backlog, new) and severity = important {this can change to also match the method your team uses to prioritize} and issuetype in (Bug) AND team = {your team here or switch the jql to use a component} ORDER BY priority DESC, status DESC, updated DESC’`

`for each of these issues - instead of just a summary, please create a detailed file with steps to resolve the issue and also identify which issues an agent can fix without needing a human to code it. Use the repository that I added to the context. You should not need to go back to jira for this information. In addition to following all of the original instructions in any other prompts regarding creating a script to add labels and details for the fixes to jira, also make sure to add an ai-fixable label for any issues that can be fixed by ai or ai-nonfixable label if it can't be automatically fixed by an agent. Ensure that the generated script is compatible with both Linux and MacOS. Add an ai-triaged label to all jira issues that are processed in the script. Ensure the full details from the file are included in the jira comment. Then create a zip file with these files.`

`Consult https://github.com/opendatahub-io/architecture-context`

---

Then download the zip file from ambient’s workspace (this zip archive represents what we’d feed to the next step). 

This zip file will contain markdown files for each jira to fix \- these markdown files can be added to each impacted jira along with the labels below to make sure AI can identify these issues for fixing.

OPTIONAL \- Follow the instructions to run the bulk\_operations.sh script that will add labels and a comment to each of the issues.  Otherwise, you can add the labels manually according to the table below.

| Triage Label | Definition / Usage |
| :---- | :---- |
| **ai-triaged** | The bug was triaged using an agent (claude/cursor/ambient). |
| **ai-fixable** | This bug was identified as fixable via AI. |
| **ai-nonfixable** | AI is not able to fix this bug without intervention \- a human should review these, identify why it isn't fixable \- add comments to the issue to help remediate these issues (or fix it manually) and possibly run through the triage workflow again. |

## 

