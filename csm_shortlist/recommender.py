"""Feature recommender for the weekly CSM shortlist.

For each account it scores every feature the account does not use yet and returns the few worth
raising in the next call. A feature's score blends four things:

  need       how strongly this account's own numbers call for the feature (0-1, rules in NeedModel)
  peer       share of comparable stores (upper half by volume, same dashboard) already using it
  readiness  1.0 when a prerequisite feature is live, 0.5 when the account would be starting cold
  ease       how quickly a CSM can get it live (static, per feature)

Evidence strings only quote numbers that exist in the dashboards; nothing is projected.
"""
import math
import statistics

WEIGHTS = {"need": 0.50, "peer": 0.25, "readiness": 0.15, "ease": 0.10}
MIN_NEED = 0.15
MAX_PICKS = 3
MAX_PER_PRODUCT = 2


class Scale:
    @staticmethod
    def log(value, ceiling):
        """0 at zero, 1 at `ceiling`, log-spaced so a 10x bigger brand is not 10x more urgent."""
        return min(1.0, math.log10(max(value, 0) + 1) / math.log10(ceiling + 1))


class FeatureSpec:
    def __init__(self, key, product, name, benefit, ask, ease, prerequisites=()):
        self.key = key
        self.product = product
        self.name = name
        self.benefit = benefit
        self.ask = ask
        self.ease = ease
        self.prerequisites = tuple(prerequisites)


class FeatureCatalog:
    """Customer-facing wording. `benefit` leads with the outcome, `ask` is the question a CSM opens with."""

    SPECS = [
        FeatureSpec("ABANDONED_CART_FLOW", "manifest", "Abandoned Checkout Recovery Nudge",
                    "Starts a chat with shoppers who leave checkout and answers what stopped them, so more carts convert.",
                    "What do you think is stopping shoppers at checkout today, and who follows up with them?", 0.9),
        FeatureSpec("PRODUCT_ADD_TO_CART", "manifest", "Add to Cart Popup Nudge",
                    "Suggests a complementary product the moment something is added to the cart, lifting order value.",
                    "Do you push cross-sells anywhere between add-to-cart and checkout?", 0.9),
        FeatureSpec("PRODUCT_REMOVE_FROM_CART", "manifest", "Removed from Cart Nudge",
                    "Catches shoppers who drop an item and offers help or an alternative before they leave.",
                    "When a shopper removes an item, do you ever find out why?", 0.8),
        FeatureSpec("CHECKOUT_COMPLETE_FLOW", "manifest", "Post-Checkout Nudge",
                    "Turns the thank-you moment into a repeat-purchase, review or referral prompt.",
                    "What happens to a customer in the first week after they order?", 0.7,
                    ("ABANDONED_CART_FLOW", "PRODUCT_ADD_TO_CART")),
        FeatureSpec("AI_HOME_SPECIALIST", "manifest", "Home Page Welcome Nudge",
                    "Greets first-time visitors on the landing page and steers them to what suits them.",
                    "How do new visitors find the right product when they land on your home page?", 0.9),
        FeatureSpec("AI_PRODUCT_SPECIALIST", "manifest", "Product Page Welcome Nudge",
                    "Answers product doubts at the exact page where the buying decision is made.",
                    "Which product questions do shoppers ask most before they buy?", 0.8),
        FeatureSpec("DISENGAGED_ON_THE_COLLECTION_PAGE", "manifest", "Collection Page Welcome Nudge",
                    "Re-engages shoppers who are scrolling collections without clicking through.",
                    "Where do visitors drop off between your collection pages and product pages?", 0.8),
        FeatureSpec("SEARCH_PAGE_RECOMMENDATIONS", "manifest", "Search Results Nudge",
                    "Recommends products when on-site search returns little or the wrong thing.",
                    "How often does on-site search leave shoppers empty-handed?", 0.6),
        FeatureSpec("PRODUCT_HISTORY_RECOMMENDATION", "manifest", "Browsing-History Recommendations",
                    "Brings shoppers back to what they viewed with personalised picks.",
                    "Do returning visitors see anything tailored to what they browsed last time?", 0.6),
        FeatureSpec("AI_FLOATING_BAR", "manifest", "AI Floating Bar",
                    "An always-visible ask-anything entry point on every page.",
                    "Can a shopper ask a question from any page right now, or only from the chat icon?", 0.8),
        FeatureSpec("ASK_AI_BAR", "manifest", "Ask AI Bar",
                    "A search-style AI entry point for shoppers who prefer typing a question to browsing.",
                    "Do your shoppers search by product name or by what they need?", 0.7),
        FeatureSpec("AI_KNOWLEDGE_BUTTON", "manifest", "Ask Me Anything Button",
                    "Answers pre-purchase questions (sizing, shipping, returns) and takes them off your support queue.",
                    "Which pre-purchase questions does your support team answer again and again?", 0.8),
        FeatureSpec("AI_PRODUCT_ASSISTANT", "manifest", "AI Product Assistant",
                    "A product expert on the product page that helps shoppers choose and lifts conversion.",
                    "Where do shoppers hesitate between two products?", 0.7),
        FeatureSpec("AI_SHOPPING_PAGE", "manifest", "AI Shopping Page",
                    "A full guided-shopping experience for shoppers who want to be walked to the right product.",
                    "Would a guided 'help me choose' journey suit your catalogue?", 0.4,
                    ("AI_PRODUCT_ASSISTANT", "AI_KNOWLEDGE_BUTTON")),
        FeatureSpec("AGENTS", "manifest", "AI Agents",
                    "Handles order status, returns and FAQs end to end, so support only sees the hard cases.",
                    "What share of your support chats are order status or returns?", 0.5),
        FeatureSpec("LEADS", "manifest", "Lead Generation",
                    "Captures email or phone inside the chat so the conversation continues on WhatsApp or email.",
                    "How many chat visitors leave without you having a way to reach them again?", 0.8),
        FeatureSpec("INTEGRATIONS", "manifest", "Integrations",
                    "Connects the helpdesk, shipping and reviews tools so the assistant answers from live data.",
                    "Which tools does your team open to answer a 'where is my order' question?", 0.6),
        FeatureSpec("WA_CAMPAIGNS", "bik", "WhatsApp Campaigns",
                    "Broadcasts offers, restocks and festive sales to opted-in customers.",
                    "What is your next sale or launch, and how will customers hear about it?", 0.9),
        FeatureSpec("WA_JOURNEYS", "bik", "WhatsApp Journeys",
                    "Automates abandoned-cart, order-update and post-purchase flows that run without a campaign each time.",
                    "Which customer moments are you still messaging by hand?", 0.7),
        FeatureSpec("EMAIL", "bik", "Email Campaigns and Journeys",
                    "Runs the same flows on email, a lower-cost channel for customers who are not on WhatsApp.",
                    "Who do you reach by email today, and who do you reach only on WhatsApp?", 0.7),
        FeatureSpec("INSTAGRAM", "bik", "Instagram DM Automation",
                    "Automates comment, story-reply and DM conversations, with AI replies, on Instagram.",
                    "How much of your enquiry volume starts as an Instagram DM?", 0.6),
        FeatureSpec("RCS", "bik", "RCS Messaging",
                    "Branded, richer messages on Android alongside WhatsApp.",
                    "Would richer branded messages help on your Android audience?", 0.5,
                    ("WA_CAMPAIGNS", "WA_JOURNEYS")),
        FeatureSpec("SMS", "bik", "SMS",
                    "Reaches customers who are not on WhatsApp for time-critical alerts.",
                    "Which alerts must reach every customer, WhatsApp or not?", 0.6,
                    ("WA_CAMPAIGNS", "WA_JOURNEYS")),
        FeatureSpec("WEBPUSH", "bik", "Web Push",
                    "Re-engages site visitors without needing their phone number.",
                    "How do you bring back visitors who never shared a number?", 0.7),
        FeatureSpec("BIK_AI_AGENT", "bik", "AI Agent on WhatsApp and Instagram",
                    "Answers customer chats on WhatsApp and Instagram and hands off to a human when needed.",
                    "How many customer chats does your team answer by hand each day?", 0.5),
        FeatureSpec("WIDGETS", "bik", "On-site Widgets",
                    "Captures leads on the site and routes them straight into WhatsApp.",
                    "How do website visitors become WhatsApp subscribers today?", 0.8),
        FeatureSpec("CRM", "bik", "CRM and Helpdesk Inbox",
                    "One shared inbox with tickets, tasks and AI reply help for the whole team.",
                    "Where does your team manage customer conversations once a customer replies?", 0.7),
        FeatureSpec("WA_CALLING", "bik", "WhatsApp Calling",
                    "Voice calls from the same inbox, for conversations that are faster to talk through.",
                    "Which conversations would close faster on a call?", 0.6, ("CRM",)),
    ]
    CROSS_SELL = {
        "manifest": FeatureSpec("MANIFEST_PRODUCT", "manifest", "Manifest AI shopping assistant",
                                "Brings the AI assistant to the store so on-site shoppers get answers and convert.",
                                "Your BIK messaging is strong; how do you help shoppers while they are on the site?", 0.5),
        "bik": FeatureSpec("BIK_PRODUCT", "bik", "BIK WhatsApp messaging",
                           "Follows up with shoppers after they leave the site, through WhatsApp campaigns and journeys.",
                           "Manifest handles on-site chats; what follows up with shoppers after they leave?", 0.5),
    }

    def __init__(self):
        self.by_key = {s.key: s for s in self.SPECS}

    def for_product(self, product):
        return [s for s in self.SPECS if s.product == product]


class AccountSignals:
    """Measured numbers for one account, summed over its matched stores."""

    def __init__(self, account):
        self.account = account
        self.manifest = [s.raw for s in account.m_stores]
        self.bik = [s.raw for s in account.b_stores]
        self.live = account.used_m() | account.used_b()

    def _sum(self, rows, column):
        return sum((r.get(column) or 0) for r in rows)

    @property
    def has_manifest(self):
        return bool(self.manifest)

    @property
    def has_bik(self):
        return bool(self.bik)

    @property
    def orders(self):
        return self.account.orders

    @property
    def avg_order_value(self):
        return self.account.gmv / self.orders if self.orders else 0

    @property
    def chat_messages(self):
        return self._sum(self.manifest, "No of messages")

    @property
    def support_sessions(self):
        return self._sum(self.manifest, "Support Metrics-totalSessions")

    @property
    def whatsapp_messages(self):
        return self._sum(self.bik, "Overall Health-totalWhatsappMessages")

    @property
    def email_messages(self):
        return self._sum(self.bik, "Overall Health-totalEmailMessages")

    @property
    def bik_messages(self):
        return self.account.messages

    @property
    def inbox_messages(self):
        return self._sum(self.bik, "crm metrics-crmMessageCount")

    @property
    def widget_leads(self):
        return self._sum(self.bik, "Widget Revenue-leadsGenerated")

    def volume(self, product):
        return self.orders if product == "manifest" else self.bik_messages


class PeerStats:
    """Adoption of each feature among comparable stores: stores in the upper half by volume."""

    def __init__(self, catalog, manifest_stores, bik_stores):
        self.adoption = {}
        self.peer_count = {}
        self._fill(catalog.for_product("manifest"), manifest_stores, lambda s: s.orders)
        self._fill(catalog.for_product("bik"), bik_stores, lambda s: s.messages)

    def _fill(self, specs, stores, volume):
        active = [s for s in stores if volume(s) > 0]
        if not active:
            return
        cutoff = statistics.median(volume(s) for s in active)
        used = [s.used_signals() for s in active if volume(s) >= cutoff]
        for spec in specs:
            self.adoption[spec.key] = sum(spec.key in u for u in used) / len(used)
            self.peer_count[spec.key] = len(used)

    def rate(self, key):
        return self.adoption.get(key, 0.0)


class NeedModel:
    """How strongly an account's own numbers call for each feature. Each rule returns (need 0-1, evidence)."""

    ORDER_WEIGHTS = {
        "ABANDONED_CART_FLOW": 1.0, "PRODUCT_ADD_TO_CART": 0.9, "AI_PRODUCT_ASSISTANT": 0.8,
        "AI_PRODUCT_SPECIALIST": 0.8, "PRODUCT_REMOVE_FROM_CART": 0.7, "CHECKOUT_COMPLETE_FLOW": 0.6,
        "SEARCH_PAGE_RECOMMENDATIONS": 0.6, "PRODUCT_HISTORY_RECOMMENDATION": 0.6, "AI_SHOPPING_PAGE": 0.5,
    }
    CHAT_WEIGHTS = {
        "AI_HOME_SPECIALIST": 0.9, "DISENGAGED_ON_THE_COLLECTION_PAGE": 0.8, "AI_KNOWLEDGE_BUTTON": 0.8,
        "LEADS": 0.8, "AI_FLOATING_BAR": 0.6, "ASK_AI_BAR": 0.6,
    }
    BIK_VOLUME_WEIGHTS = {"INSTAGRAM": 0.5, "RCS": 0.5, "SMS": 0.4, "WEBPUSH": 0.3, "WIDGETS": 0.5}

    def __init__(self, signals):
        self.s = signals
        self.rules = {k: self._orders_rule(w) for k, w in self.ORDER_WEIGHTS.items()}
        self.rules.update({k: self._chat_rule(w) for k, w in self.CHAT_WEIGHTS.items()})
        self.rules.update({k: self._bik_volume_rule(w) for k, w in self.BIK_VOLUME_WEIGHTS.items()})
        self.rules.update({
            "AGENTS": self._agents, "INTEGRATIONS": self._integrations, "WA_CAMPAIGNS": self._campaigns,
            "WA_JOURNEYS": self._journeys, "EMAIL": self._email, "BIK_AI_AGENT": self._bik_agent,
            "CRM": self._crm, "WA_CALLING": self._calling,
        })

    def need(self, key):
        return self.rules[key]()

    def _orders_text(self):
        text = f"{self.s.orders:,} orders in Sep"
        if self.s.avg_order_value:
            text += f" at about ${self.s.avg_order_value:,.0f} each"
        return text

    def _orders_rule(self, weight):
        return lambda: (weight * Scale.log(self.s.orders, 20000), self._orders_text())

    def _chat_rule(self, weight):
        return lambda: (weight * Scale.log(self.s.chat_messages, 20000),
                        f"{self.s.chat_messages:,} shopper chat messages in Sep")

    def _bik_volume_rule(self, weight):
        return lambda: (weight * Scale.log(self.s.bik_messages, 1_000_000),
                        f"{self.s.bik_messages:,.0f} messages sent in Sep")

    def _agents(self):
        if self.s.support_sessions:
            return (0.6 + 0.4 * Scale.log(self.s.support_sessions, 2000),
                    f"{self.s.support_sessions:,} support conversations in Sep and no AI Agent set up")
        return 0.35 * Scale.log(self.s.chat_messages, 20000), f"{self.s.chat_messages:,} shopper chat messages in Sep"

    def _integrations(self):
        if "AGENTS" in self.s.live:
            return 0.9, "AI Agents are live but nothing connects them to live order or helpdesk data"
        return 0.3 * Scale.log(self.s.orders, 20000), self._orders_text()

    def _campaigns(self):
        if "WA_JOURNEYS" in self.s.live:
            return 0.85, "WhatsApp journeys already run; campaigns are the other half of the channel"
        return 0.6 * Scale.log(self.s.bik_messages, 1_000_000), f"{self.s.bik_messages:,.0f} messages sent in Sep"

    def _journeys(self):
        if "WA_CAMPAIGNS" in self.s.live:
            return 0.85, "WhatsApp campaigns already run; journeys would automate the repeat moments"
        return 0.6 * Scale.log(self.s.bik_messages, 1_000_000), f"{self.s.bik_messages:,.0f} messages sent in Sep"

    def _email(self):
        return (0.8 * Scale.log(self.s.whatsapp_messages, 1_000_000),
                f"{self.s.whatsapp_messages:,.0f} WhatsApp messages sent in Sep and no email at all")

    def _bik_agent(self):
        if self.s.inbox_messages:
            return (0.5 + 0.5 * Scale.log(self.s.inbox_messages, 5000),
                    f"{self.s.inbox_messages:,} customer messages answered by the team in the inbox in Sep")
        return 0.4 * Scale.log(self.s.bik_messages, 1_000_000), f"{self.s.bik_messages:,.0f} messages sent in Sep"

    def _crm(self):
        if "BIK_AI_AGENT" in self.s.live:
            return 0.7, "An AI agent is live but the team has no shared inbox to take over handoffs"
        return 0.4 * Scale.log(self.s.bik_messages, 1_000_000), f"{self.s.bik_messages:,.0f} messages sent in Sep"

    def _calling(self):
        if "CRM" in self.s.live:
            return 0.5, "The inbox is already live, so calling needs no new setup"
        return 0.2, "No inbox in use yet"


class Recommendation:
    def __init__(self, spec, need, evidence, peer, readiness, peer_count):
        self.spec = spec
        self.need = need
        self.evidence = evidence
        self.peer = peer
        self.readiness = readiness
        self.peer_count = peer_count
        self.score = (WEIGHTS["need"] * need + WEIGHTS["peer"] * peer
                      + WEIGHTS["readiness"] * readiness + WEIGHTS["ease"] * spec.ease)

    @property
    def peer_note(self):
        if self.peer_count < 5:
            return ""
        return f"{self.peer:.0%} of {self.peer_count} comparable stores already use it"


class Recommender:
    def __init__(self, catalog, peers):
        self.catalog = catalog
        self.peers = peers

    def rank(self, account):
        """Every viable recommendation for the account, best first."""
        signals = AccountSignals(account)
        if not (signals.has_manifest or signals.has_bik):
            return []
        model = NeedModel(signals)
        found = []
        for product, present in (("manifest", signals.has_manifest), ("bik", signals.has_bik)):
            if present:
                found += self._for_product(product, signals, model)
        if signals.has_manifest != signals.has_bik:
            found.append(self._cross_sell(signals))
        return sorted(found, key=lambda r: -r.score)

    def pick(self, account):
        """The few to raise in the next call: at most two per product so one product doesn't crowd out the other."""
        picks, per_product = [], {}
        for rec in self.rank(account):
            if per_product.get(rec.spec.product, 0) >= MAX_PER_PRODUCT:
                continue
            per_product[rec.spec.product] = per_product.get(rec.spec.product, 0) + 1
            picks.append(rec)
            if len(picks) == MAX_PICKS:
                break
        return picks

    def strength(self, account):
        """Mean score of the two best picks; 0.3 when there is no usage data to recommend from."""
        top = [r.score for r in self.pick(account)[:2]]
        return sum(top) / len(top) if top else 0.3

    def _for_product(self, product, signals, model):
        out = []
        for spec in self.catalog.for_product(product):
            if spec.key in signals.live:
                continue
            need, evidence = model.need(spec.key)
            if need < MIN_NEED:
                continue
            ready = 1.0 if not spec.prerequisites or signals.live & set(spec.prerequisites) else 0.5
            out.append(Recommendation(spec, need, evidence, self.peers.rate(spec.key), ready,
                                      self.peers.peer_count.get(spec.key, 0)))
        return out

    def _cross_sell(self, signals):
        missing = "bik" if signals.has_manifest else "manifest"
        spec = self.catalog.CROSS_SELL[missing]
        if missing == "bik":
            need = 0.5 + 0.5 * Scale.log(signals.orders, 20000)
            evidence = f"{signals.orders:,} Manifest orders in Sep and no WhatsApp follow-up on BIK"
        else:
            need = 0.5 + 0.5 * Scale.log(signals.bik_messages, 1_000_000)
            evidence = f"{signals.bik_messages:,.0f} BIK messages sent in Sep and no on-site Manifest assistant"
        return Recommendation(spec, need, evidence, 0.5, 1.0, 0)
