# Kataru Demo Script (3 Minutes)

## Setup
- Open https://voice-ai-agent-37b8.onrender.com
- Login with: `demo` / `demo123`
- Click the microphone button to start voice chat
- Have a quiet environment for clean audio

---

## Minute 1: Multilingual + Code-Switching + Info Collection

**[0:00] GREETING (Hindi)**
> Say: "Namaste"

AI responds in Hindi: "Namaste! Main Kataru hoon..."

**[0:10] NAME COLLECTION**
> Say: "Mera naam Rahul hai"

AI acknowledges and asks about the issue.

**[0:20] ISSUE + CODE-SWITCHING (Hindi → English)**
> Say: "Mera electricity bill bahut zyada aaya hai. Last month it was 2000 but this month it shows 5000"

Notice: AI switches to English when you switch. It identifies the issue type (billing).

**[0:40] DETAILS COLLECTION**
> Say: "It happened in the January bill. My account number is 12345. I live in Delhi."

AI collects: date, account, location.

---

## Minute 2: Confirmation + Interruption + Emotion

**[1:00] CONFIRMATION**
AI repeats back all collected info: "Let me confirm: Name is Rahul, issue is billing, electricity bill jumped from 2000 to 5000 in January, account 12345, Delhi. Is this correct?"

> Say: "Haan, correct hai"

**[1:20] INTERRUPTION HANDLING**
While AI is speaking, interrupt:
> Say: "Ruko ruko, ek aur cheez — last week bhi maine complaint ki thi but koi response nahi mila"

AI stops talking immediately and acknowledges the interruption: "Main sun rahi hoon. Bataiye."

**[1:40] ANGRY EMOTION DETECTION**
> Say: "This is terrible! I have called three times and nobody is helping. I am very frustrated with this service."

Notice: AI detects angry emotion, acknowledges frustration, adapts tone.

---

## Minute 3: Escalation + Dashboard

**[2:00] ESCALATION**
> Say: "I want to speak to a human agent. This is not getting resolved."

AI creates a ticket and offers callback: "I will connect you with a specialist. Would you prefer a callback?"

> Say: "Yes, schedule a callback for tomorrow at 3 PM"

**[2:20] TICKET CREATION**
AI confirms: ticket created with full context — name, issue, emotion (angry), language (Hindi/English), callback scheduled.

**[2:30] SWITCH TO DASHBOARD**
Click "Dashboard" in the sidebar.
Show:
- Total tickets count
- Escalation rate
- Language distribution (Hindi/English)
- Emotion breakdown (Angry detected)
- Recent tickets list

**[2:45] SWITCH TO TICKETS**
Click "Tickets" in the sidebar.
Show the ticket that was just created with:
- Status: Escalated
- Issue type: Billing
- Language: Hindi
- Priority: High

**[3:00] END**
"Kataru — Build AI That Speaks, Listens, and Acts."

---

## Key Points to Highlight During Demo

1. **Multilingual**: Started in Hindi, switched to English mid-sentence
2. **Interruption handling**: AI stopped when interrupted mid-response
3. **Emotion detection**: Detected anger, adapted response style
4. **Information confirmation**: Repeated back all collected details
5. **Context preservation**: Escalated with full conversation summary
6. **Ticketing**: Created ticket with all context
7. **Safety**: Would refuse medical/legal/emergency advice if asked
8. **Dashboard**: Real-time analytics showing the interaction data
