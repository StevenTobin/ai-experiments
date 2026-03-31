## AI Bug Bash \- March 23 \-27

---

## **1\. Objective & Scope**

The primary goal of this event is to leverage AI to target bugs identified for **full automation**, evaluate AI performance in identifying and fixing issues, and significantly reduce the backlog.

* **Continuous Improvement:** Engineer feedback will be used to refine AI tooling capabilities for future cycles.  
* **Quality Mandate:** The entire week is dedicated to triaging, fixing, and validating bugs without sacrificing quality. Every fix must be root-caused, peer-reviewed, and landed without introducing regression risks.

## **2\. Operational Workflow**

Teams are required to use AI throughout the lifecycle of the bug bash.

**Identification & Setup**

* **AI Triage:** Each team is required to use AI to determine if bugs can be fully automated.  The [Get Started](?tab=t.ttpl2uu6uouo) tab walks through using Ambient Code Platform to perform the triage.  
* **AI Labeling**:  Each team is required to use the labels for the [triage](?tab=t.ttpl2uu6uouo#bookmark=id.24tymavqs06a) process as well as [tracking](#bookmark=id.clrp9qf901pw). (If using Ambient, the tool will add labels for triage.)  
* **AI Remediation**: Any bugs identified as fully automatable should be fixed and verified by AI. [Guidance](?tab=t.0) is given on how to configure repositories for AI-assisted fix and verification.  
* **Deep Work:** Wednesday is designated as a "no-meeting" day to allow for uninterrupted execution.

### **Hybrid Verification Model**

We utilize a two-tier verification system to ensure maximum reliability:

1. **Phase 1: AI Verification (Initial):** AI runs automated tests, performs regression checks, and validates the initial fix.  
2. **Phase 2: Human Validation (Final):** Humans perform peer reviews, code reviews, and manual acceptance testing.

### **Success Criteria**

* **Pass:** Requires approval from **both** AI verification and human final validation.  
* **Fail:** If **either** AI verification or human validation rejects the fix, the bug is marked as failed.

## **3\. Tracking & Taxonomy**

To maintain an accurate 100% execution baseline, use the following labels to categorize bug outcomes:

| Outcome Label | Definition / Usage |
| :---- | :---- |
| **ai-fully-automated** | The bug was fixed and verified using only AI tools. |
| **ai-accelerated-fix** | The bug was fixed and verified using AI tools after more than one attempt.  |
| **ai-could-not-fix** | AI attempted a fix but failed to produce a viable solution or found it too complex. |
| **ai-verification-failed** | AI generated a fix, but its own automated tests or regression checks failed. |
| **regressions-found** | Added after merge when a fix introduces a new defect elsewhere in the codebase. Never applied before merge, if that happens it is ai-verification-failed. Counted in event metrics if added by April 2\. |

## **4\. Reporting & Feedback**

* **Beginning Report:** All bugs flagged as ai-triaged  
  * Each of the triaged bugs should be flagged as either ai-fixable or ai-nonfixable  
* **End Report:** Analysis of label distributions (Pass/Fail rates).  
  * Once AI has been attempted on all of the ai-fixable bugs, a result label of either ai-fully-automated, ai-could-not-fix, ai-verification-failed, regressions-found  
* **Post-Event Survey:** **One survey per engineer** at the end of the event to capture high-level themes, tool friction, and "best/worst" examples.

