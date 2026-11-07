> Copied from the Google Doc on Sat Nov 7, 2026. The Google Doc is the source of truth: https://docs.google.com/document/d/12GdwwBdgdPt7WwjoHmqkMiZSQZdvNUMSRqYMxMcIwZM/edit

# **RollCall Voice PRD (HackUTD 2026\)**

November 7, 2026 · Rajat Singh

## **Summary**

RollCall Voice is an AI agent that phones every customer in a disaster zone, works out who needs help most, and gets that help moving. It keeps calling back until every household is reached.

> * **Pitch:** "When a tornado hits Dallas, thousands of people are in its path. RollCall checks on every one of them in minutes and finds who needs help first."  
> * **Tracks:** NVIDIA "Use Nemotron Beyond a Chatbot" and State Farm "The Worst Day, at Scale".  
> * **Deadline:** submit by 12:00 pm on November 8, 2026; judging runs 1:00 to 4:00 pm.  
> * **Demo moment:** the judge's phone rings, they talk to the agent, and their home turns red on the map while help is sent.

## **Problem**

After a big storm, the people who most need help are often the hardest to reach, and checking on everyone by hand takes most of a day.

> * **Too slow:** one person can make maybe 20 to 30 check-in calls an hour (our estimate), so a few thousand homes swamp a call center.  
> * **Wrong order:** calls usually go down a list, so an older person alone on an oxygen machine can wait behind someone who is fine.  
> * **People get missed:** anyone who doesn't answer, doesn't text, or struggles with English easily falls through.  
> * **Scattered info:** call notes, crew schedules, shelter beds and claims live in different places.

State Farm's challenge says the same thing: find the most urgent needs, coordinate help, and make sure no one is overlooked.

## **Goals, non-goals and success metrics**

By Sunday noon we ship one working loop (alert, calls, help sent, follow-up) plus measured results from a 2,000-household test.

### **Goals (must have)**

> 1. A real phone call where Nemotron talks with a person and records their needs.  
> 2. A manager agent that starts from a weather alert, decides who to call first, and sends help.  
> 3. A follow-up loop, so no household stays unreached.  
> 4. A live map dashboard showing every household's status and every agent action.  
> 5. Measured results from the fake-resident test, compared with calling in list order.

### **Non-goals (not this weekend)**

> * Real customer data, real dispatch systems or a real State Farm claims system (all mocked).  
> * Replacing 911 or human responders.  
> * A mobile app, user accounts or production-grade security.

### **Success metrics (targets for the test)**

| Metric | Target |
| :---- | :---- |
| Urgent households found (P1 \+ P2) | 95% or more |
| Time until every urgent household has help on the way | Much faster than list order (report the ratio) |
| False alarms (marked urgent but fine) | 10% or less |
| Households reached or escalated to a person | 100% |
| Delay before the agent replies on a call | Under about 1.5 seconds |
| Live call at judging | Works on the first try |

## **Users**

Three people use RollCall, and each needs something different from it.

| User | Who they are | What they need |
| :---- | :---- | :---- |
| Response coordinator | State Farm catastrophe team member running the response | One screen: who is safe, who needs help, what was sent, what needs approval |
| Policyholder | Customer inside the warning area, often older or living alone | A short, calm call that asks the right questions and says what happens next |
| Human dispatcher | Person who takes over emergencies | A clean handoff: who, where, what's wrong, what's already done |

## **Track fit**

RollCall meets all four of NVIDIA's rules and answers State Farm's challenge point by point, so one build competes in both tracks.

| NVIDIA rule | How RollCall meets it |
| :---- | :---- |
| Beyond a chatbot | No chat box. A weather alert starts it, and it runs the response by itself. |
| Multi-step workflows | Plan, call, understand, send help, follow up, and re-plan as answers come in. |
| Tool integration | Weather alerts, phone calls, text messages and maps, plus booking tools for crews, shelters, adjusters and claims. |
| Real-world applicability | Disaster check-ins are real work done slowly by hand today. |

| State Farm asks us to | RollCall's answer |
| :---- | :---- |
| Identify those with the most urgent needs | Risk-ranked calling, plus need detection on every call |
| Coordinate assistance | Sends crews, shelter beds, adjusters and claims; costly items wait for approval |
| Ensure no one is overlooked | Call again, then text, then an in-person check, until "not reached" is zero |

**Why Nemotron:** open models fast and cheap enough for thousands of calls at once. A small model talks, a big model plans, and NVIDIA's own voice models are a stretch goal.

## **How it works**

A weather alert starts the agent, and every household in the area moves through the same loop until it ends safe, helped, or with a human.

> 1. A weather alert comes in.  
> 2. The agent finds every home inside the warning area.  
> 3. It ranks the homes by risk.  
> 4. It calls them in waves, highest risk first.  
> 5. If nobody answers, the follow-up ladder takes over (call again, text, then an in-person visit), and the home goes back into the call queue.  
> 6. If someone answers, the agent works out their needs and how urgent they are.  
> 7. It acts on the urgency level:  
   * **Emergency (P1):** tell them to call 911 and hand them to a human right away.  
   * **Needs help (P2 to P3):** send a crew, shelter bed or adjuster, and open a claim.  
   * **Safe (P4):** mark them safe.  
> 8. It texts the next steps, updates the map, and repeats until every home is reached.

Because anyone who doesn't answer goes back into the call queue, the "not reached" count only ever goes down.

## **Functional requirements**

Fourteen must-haves make the full loop work; three stretch items come only after every checkpoint in the timeline is passed.

| \# | Part | Requirement | Priority |
| :---- | :---- | :---- | :---- |
| F1 | Alert | Load a saved warning area (GeoJSON) for the demo | Must |
| F2 | Alert | Read live alerts from the National Weather Service | Stretch |
| F3 | Households | Load 2,000 fake households with location, risk details and phone | Must |
| F4 | Who's affected | Find every household inside the warning area | Must |
| F5 | Priority | Score each household's risk in code and order the calls | Must |
| F6 | Calls | Place real calls through the phone platform; run simulated calls in parallel for the test | Must |
| F7 | Voice agent | Ask the check-in questions, understand free speech, save needs with tools | Must |
| F8 | Emergencies | P1: tell them to call 911 and hand them to a human right away | Must |
| F9 | Send help | Assign crews, shelter beds and adjusters; open a claim (all mocked) | Must |
| F10 | Texts | Text each person what happens next | Must |
| F11 | Follow-up | No answer: call again, then text, then an in-person check | Must |
| F12 | Approvals | Costly actions wait for the coordinator's OK | Must |
| F13 | Dashboard | Live map, counters, transcripts, action feed, approvals queue | Must |
| F14 | Test runner | Run 2,000 fake residents and compute metrics against list order | Must |
| F15 | Spanish | Detect Spanish and switch languages mid-call | Stretch |
| F16 | Photos | Read damage photos people text in (Nemotron Nano Omni) | Stretch |
| F17 | NVIDIA voice | Nemotron Speech and Magpie voice in place of the platform's speech | Stretch |

## **Voice call design**

Every call is short (target under 2 minutes), says up front that it's an automated assistant, and ends with a clear next step.  
**Opening line:** "Hi, this is RollCall, an automated assistant calling for State Farm about the tornado warning near you. Is this Maria? Are you safe right now?"

### **Questions, in order**

Skip anything already answered.

> 1. Is anyone hurt or in danger right now?  
> 2. Do you have power? Does anyone rely on a medical device, or medicine that needs power or a fridge?  
> 3. Is your home damaged? Can you stay there tonight?  
> 4. Do you need help leaving, supplies, or a place to stay?  
> 5. Who is with you, including kids or pets?

### **Urgency levels**

| Level | Meaning | Examples | What happens |
| :---- | :---- | :---- | :---- |
| P1 Emergency | Life in danger now | Injury, trapped, gas smell, fire, medical device dead | Tell them to call 911, hand to a human now, mark red |
| P2 Urgent | Needs help within hours | Oxygen or CPAP on battery, older person alone with no power, home unlivable | Help goes out in the first wave, mark orange |
| P3 Needs help | Within a day | Roof leak, tree down, needs supplies | Book a crew or adjuster, open a claim, mark yellow |
| P4 Safe | No needs | None | Mark green, text a number to call back |

### **Tools the voice agent can use during a call**

| Tool | What it does |
| :---- | :---- |
| record\_need(category, details, urgency) | Saves a need to the household |
| flag\_urgent(level, reason) | Raises the household to P1 or P2 right away |
| transfer\_to\_human(reason) | Hands the call to a human dispatcher |
| send\_text(message) | Sends a confirmation or check-in text |
| end\_call(summary) | Ends the call and passes a summary to the manager agent |

### **Safety rules**

> * Always say it's an automated assistant; transfer to a human whenever asked.  
> * Never give medical advice. For any danger, say "please hang up and call 911" and transfer to a human.  
> * Never accept "I'm fine" at face value: ask about power-dependent devices and who is home.  
> * Catch downplaying: "I'm fine, honey, my oxygen machine just keeps beeping" is P2 or P1, not safe.  
> * Use the fastest Nemotron model with short replies, so the pause before each answer stays under about 1.5 seconds.

## **Manager agent design**

The manager agent runs the whole response: it plans who to call, reacts to every call result, sends help, and keeps going until everyone is reached. Nemotron decides and explains; plain code does the scoring, matching and money math.

### **The loop (LangGraph)**

> 1. **Alert in:** find every household inside the warning area.  
> 2. **Score risk in code:** age 75+, lives alone, medical device that needs power, mobility limits, no car, kids under 5, language needs, distance to the warning center.  
> 3. **Plan call waves:** highest risk first. Nemotron 3 Super reviews the plan and states it in one line, for example "312 high-risk homes first, in 3 waves."  
> 4. **Launch calls** up to the phone-line limit.  
> 5. **On each call result:** check the urgency, choose actions, call the tools.  
> 6. **Re-plan** when things change: a new P1, all crews busy, a shelter full.  
> 7. **Follow up** on anyone not reached (see the ladder below).  
> 8. **Post a short status update** for the coordinator every 10 minutes.

### **Tools for sending help**

| Tool | What it does |
| :---- | :---- |
| dispatch\_crew(household, reason) | Sends the nearest free crew for a wellness check, generator or supplies |
| reserve\_shelter\_bed(household, people) | Holds beds at the nearest shelter with space |
| schedule\_adjuster(household, window) | Books an adjuster visit for damage |
| open\_claim(household, details) | Opens a claim with what the caller already said filled in (mock) |
| request\_approval(action, cost, reason) | Puts a costly action in the coordinator's queue |
| schedule\_followup(household, minutes, channel) | Sets the next call or text |

Matching rule (in code): the nearest available crew, shelter or adjuster with capacity. The agent chooses *what kind* of help and explains why.

### **Follow-up ladder**

| Step | When | Action |
| :---- | :---- | :---- |
| 1 | First call not answered | Call again after 10 minutes |
| 2 | Still no answer | Text: "Reply 1 safe, 2 need help, 3 emergency" |
| 3 | No reply 20 minutes later | Call one last time |
| 4 | Still nothing | Send a crew for an in-person check; flag it for the coordinator |

**Approvals:** emergency handoffs and P2 help go out automatically. Hotel vouchers, generator deliveries, or anything over a set dollar amount waits for the coordinator.  
**Thinking:** turned on for planning and re-planning, off for quick tool calls, so the agent stays fast.

## **Data model and fake data**

Everything runs on fake data we generate; no real customer information is used anywhere.

| Table | Key fields |
| :---- | :---- |
| households | id, name, phone, address, lat, lng, age, lives\_alone, medical\_device, mobility, has\_car, kids\_under\_5, language, risk\_score, status |
| hidden\_truth (test only) | household\_id, true\_urgency, true\_needs, persona\_style (normal, downplays, hard of hearing, Spanish only, confused, never answers) |
| calls | id, household\_id, started\_at, ended\_at, outcome, transcript, summary |
| needs | id, household\_id, category, details, urgency |
| resources | id, type (crew, shelter, adjuster, generator), location, capacity, free\_at |
| actions | id, household\_id, type, status, cost, needs\_approval, approved\_by |
| events | id, time, type, payload (feeds the dashboard and lets us replay a run) |

### **Fake data plan (our choices; tune as needed)**

> * 2,000 households around Richardson, Garland and Plano, inside and near the warning area.  
> * Roughly 5% P1 to P2, 20% P3, the rest safe. Make it hard enough that calling in list order falls well behind.  
> * 30 tricky personas for the trap set (see Testing and evaluation).  
> * 6 crews, 3 shelters with bed limits, 4 adjusters, 2 generators.  
> * One saved warning area (a GeoJSON polygon) over the neighborhood for the demo.  
> * Household status values: not called, calling, safe, needs help, urgent, emergency, no answer, escalated.

## **Architecture and tech stack**

A managed phone platform handles the calling and speech, Nemotron does the thinking, and a small Python backend holds the tools, data and live updates.

### **How the parts connect**

> * The phone platform (Vapi) places the calls and asks Nemotron for every reply.  
> * During a call, the phone platform sends in-call tool calls to the backend API (FastAPI).  
> * The manager agent (LangGraph) asks Nemotron 3 Super for every plan and calls the backend's tools.  
> * The backend does scoring, matching and costs in code, pulls weather alerts, sends texts, hands emergencies to a human dispatcher, and writes every step to the database and event log.  
> * The dashboard shows live updates from the event log and sends approvals back.  
> * The test runner sends simulated calls through the same manager agent, with Nemotron playing 2,000 residents.

### **Stack**

| Part | Choice | Why |
| :---- | :---- | :---- |
| Phone calls and speech | Vapi (custom LLM pointed at Nemotron); Twilio or Photon as backup | Fastest path to a real call; Vapi accepts any OpenAI-compatible model endpoint |
| Models | Nemotron through build.nvidia.com (free key); OpenRouter free tiers as backup | Free, OpenAI-compatible, and required for the NVIDIA track |
| Manager agent | LangGraph (Python) | The team already knows it |
| Backend | FastAPI and SQLite, WebSocket for live updates | Small, quick to build |
| Text messages | Twilio SMS (or the phone platform's texting) | Confirmations and the follow-up ladder |
| Weather alerts | National Weather Service API (free) or a saved warning polygon | Real data for the trigger |
| Dashboard | React and Leaflet (Lovable can speed up the UI) | Map-first view for the coordinator and judges |
| Stretch | NVIDIA Nemotron Voice Agent blueprint (Nemotron Speech and Magpie voice) | An all-NVIDIA voice stack earns extra points with NVIDIA judges |

### **Which Nemotron model does which job**

| Job | Model | Thinking |
| :---- | :---- | :---- |
| Manager: plan, triage, re-plan, status updates | Nemotron 3 Super | On |
| Voice on the phone | Nemotron 3 Nano or 3.5 Lightning | Off (speed first) |
| Fake residents in the test | Nemotron 3 Nano | Off |
| Damage photos (stretch) | Nemotron 3 Nano Omni | Off |

## **Dashboard**

One screen tells the coordinator, and the judges, the whole story at a glance without anyone explaining it.

> * **Map:** every household as a dot: gray not reached, green safe, yellow needs help, orange urgent, red emergency. The warning area is shaded.  
> * **Counters:** called, reached, urgent, help sent, not reached yet.  
> * **Live calls:** transcripts streaming for calls in progress.  
> * **Agent feed:** every plan, decision and tool call in plain words, for example "Sent crew 3 to 1412 Oak St: oxygen machine without power."  
> * **Approvals queue:** approve or reject costly actions in one click.  
> * **Household panel:** click a dot to see needs, call history, actions taken and the next follow-up.  
> * **Test mode:** run the 2,000-resident test and show the results next to the list-order baseline.

## **Testing and evaluation**

We prove RollCall works with a 2,000-household test in which every fake resident has a hidden situation we can check the agent against.

### **How the test runs**

> 1. Generate the households and their hidden truths (see Data model and fake data).  
> 2. Nemotron plays each resident over text, in that persona's style, so hundreds of "calls" can run at once.  
> 3. Run the same city twice: (a) RollCall with risk-ranked calling; (b) the baseline, calling in list order.  
> 4. Use a simulated clock: about 2 minutes per call, with N phone lines in parallel (state N in the results, for example 100).  
> 5. Score both runs against the hidden truth.

### **Metrics**

| Metric | How it's measured |
| :---- | :---- |
| Urgent found | P1 \+ P2 households the agent flagged, divided by all true P1 \+ P2 |
| Time to help | Simulated minutes until every true P1 \+ P2 has help on the way |
| False alarms | Households flagged urgent that were really P3 or P4, divided by all flagged urgent |
| Coverage | Households reached or escalated, divided by all households in the area |
| Call length | Average minutes per call |
| Cost | Model tokens per household |

**Trap set (30 personas):** someone who downplays ("I'm fine, honey"), someone hard of hearing, Spanish only, confused or angry, "call my daughter instead", and a hang-up mid-call. Report how many traps the agent handles correctly.  
**Live check:** measure the reply delay on real calls.  
**Honesty rule:** every number in the pitch comes from our own runs, and we say plainly that the 2,000-call test is simulated.

## **2-minute demo script and judge Q\&A**

The demo is built around one moment: the judge's phone rings, they talk to the agent, and they watch their own home change on the map.

| Time | What we show | What we say |
| :---- | :---- | :---- |
| 0:00 | Map with 2,000 homes | "When a tornado hits Dallas, thousands of people are in its path. One person can check on maybe 30 an hour." |
| 0:15 | Click "Tornado warning" | "The agent finds everyone inside, ranks who's most at risk, and starts calling." |
| 0:30 | Dots change color, counters climb | "Nobody typed anything. It's running the response by itself." |
| 0:45 | The judge's phone rings | The judge talks to the agent; their dot turns orange; help appears in the feed; a text arrives. |
| 1:20 | Trap case, side by side | "Keyword logic marked her safe. Nemotron caught the oxygen machine." |
| 1:35 | Results panel | "It found X of Y urgent cases in Z minutes. Calling in list order took W." |
| 1:50 | Close | "Every home reached. No one left behind." |

### **Likely judge questions**

> * **Why AI and not a script?** The trap case: people describe emergencies in their own words. Add free-speech understanding and re-planning when things change.  
> * **Why Nemotron?** Open, fast and cheap enough for thousands of calls at once. A small model talks, a big model plans, and it works with NVIDIA's own voice models.  
> * **Is it safe?** It says it's automated, sends any danger to 911 and a human, and costly actions need approval. Real customers would opt in ahead of time.  
> * **What's real and what's simulated?** The call, the agent and the tools are real. Households, dispatch and claims are mocked, and the 2,000-call test is simulated.  
> * **Does it scale?** The limit is phone lines, not the agent. State the number of lines used in the test.

## **Team split and timeline**

Four owners work in parallel, and the 6 pm check on one real call decides whether we stay with voice or switch to text check-ins.

### **Build phases**

| Phase | When | What gets built |
| :---- | :---- | :---- |
| First call | Sat 3 to 6 pm | Keys, fake data, one real call |
| Core loop | Sat 6 to 11 pm | Manager agent, tools, basic map |
| Connect | Sat 11 pm to Sun 4 am | Live call on the map, texts, follow-ups |
| Prove it | Sun 4 to 9 am | 2,000-resident test and the numbers |
| Ship | Sun 9 am to 12 pm | Video, Devpost, rehearse the demo 10 times |

If the 6 pm call test fails, switch to text check-ins and keep the rest of the plan unchanged.

### **Owners**

| Person | Owns | Done when |
| :---- | :---- | :---- |
| 1: Voice | Phone agent, in-call tools, call-to-backend webhooks | A real call saves a need that shows up on the map |
| 2: Rajat, manager and backend | LangGraph loop, risk scoring, matching, follow-ups, approvals, event stream | Alert, calls, help sent and follow-ups run end to end |
| 3: Testing | Fake data, Nemotron residents, test runner, metrics | Results table for both runs |
| 4: Dashboard | Map, counters, transcripts, agent feed, approvals | A judge can follow the story without us talking |

### **Checkpoints**

> * Sat 6:00 pm: one real call works with Nemotron as the brain; fake data generated.  
> * Sat 11:00 pm: alert, calls and help sent run end to end; Devpost draft submitted.  
> * Sun 4:00 am: a live call updates the map; texts, follow-ups and approvals work.  
> * Sun 9:00 am: test results done; feature freeze.  
> * Sun 12:00 pm: video, Devpost and repo final; submitted.

## **Risks, backups and safety**

Voice is the biggest risk, so we test it first and keep a text-message fallback ready.

| Risk | Backup plan |
| :---- | :---- |
| Calls feel slow | Fastest Nemotron model for talking, short replies, test latency first |
| Trial phone accounts may only call verified numbers | Verify a teammate's phone and hand it to the judge |
| Nemotron API rate limits | OpenRouter free tiers as backup; cache test runs |
| A tool call comes back broken | Validate, retry once, then fall back to a simple rule |
| Voice still not solid by about 10 pm | Switch to text check-ins; the agent, map and numbers stay |
| Venue Wi-Fi drops | Phone hotspot plus a recorded backup video of a good call |
| Too much scope | Stretch items only after every checkpoint is done |

### **Safety and privacy rules**

> * Fake data only; no real customer information.  
> * Only call people who agreed (our team, the judge in front of us). In real life, customers would opt in.  
> * The agent always says it's automated. Any danger goes to 911 and a human.  
> * No medical advice; costly actions need a human's approval.  
> * Every decision is logged with its reason, so a person can review it.

## **Submission checklist and open questions**

A missed Devpost step can disqualify a working project, so check these off as you go.

### **Checklist**

> * New public GitHub repo created after hacking started (Sat 12:00 pm); all code written this weekend.  
> * README: what we built, how to run it, and the AI coding tools we used (MLH rule).  
> * Devpost project submitted by the 11:00 pm Saturday deadline.  
> * Tracks selected: NVIDIA and State Farm.  
> * Every teammate invited on Devpost.  
> * 2-minute demo video at a public link.  
> * Screenshots or a GIF of the map and a live call.  
> * Final submit before 12:00 pm Sunday.  
> * Demo rehearsed 10 times: one person drives, one person talks.

### **Open questions**

> * Who is on the team, and who takes each of the four roles?  
> * Which phone platform do we have credits for: Vapi, Twilio or Photon?  
> * Do we try NVIDIA's own voice stack, or keep the platform's speech?  
> * Spanish: stretch goal or skip?

### **Sources**

> * [HackUTD 2026: tracks and schedule](https://zeroday.hackutd.co/)  
> * [HackUTD Devpost guide](https://hackutd-tech-guide-git-springbranchupdated-acmutd.vercel.app/dayof/devpost)  
> * [MLH standard hackathon rules](https://github.com/MLH/mlh-policies/blob/main/standard-hackathon-rules.md)  
> * [Vapi: custom LLM](https://docs.vapi.ai/assistants/custom-llm)  
> * [NVIDIA Nemotron Voice Agent blueprint](https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent)  
> * [NVIDIA Nemotron models and access](https://www.nvidia.com/en-us/ai-data-science/foundation-models/nemotron/llm-info/)  
> * [Phone voice delay people tolerate (about 1.5 seconds)](https://www.mindstudio.ai/blog/pipecat-phonellm-alpha-1-voice-agent-model)