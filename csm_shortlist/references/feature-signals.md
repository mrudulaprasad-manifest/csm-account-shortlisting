# Manifest feature signals (17)

A feature counts as in use when its `Interaction Rate-<TYPE>-total-sessions` column is above 0 for the month, except the last three.

| Feature | `<TYPE>` |
|---|---|
| Abandoned Checkout Recovery Nudge | ABANDONED_CART_FLOW |
| Add to Cart Popup Nudge | PRODUCT_ADD_TO_CART |
| Removed from Cart Nudge | PRODUCT_REMOVE_FROM_CART |
| Post-Checkout Nudge | CHECKOUT_COMPLETE_FLOW |
| Home Page Welcome Nudge | AI_HOME_SPECIALIST |
| Product Page Welcome Nudge | AI_PRODUCT_SPECIALIST |
| Collection Page Welcome Nudge | DISENGAGED_ON_THE_COLLECTION_PAGE |
| Search Results Nudge | SEARCH_PAGE_RECOMMENDATIONS |
| Browsing-History Recommendations | PRODUCT_HISTORY_RECOMMENDATION |
| AI Floating Bar | AI_FLOATING_BAR |
| Ask AI Bar | ASK_AI_BAR |
| Ask Me Anything Button | AI_KNOWLEDGE_BUTTON |
| AI Product Assistant | AI_PRODUCT_ASSISTANT |
| AI Shopping Page | AI_SHOPPING_PAGE |
| AI Agents | `Support Metrics-agentsCreated` > 0 |
| Lead Generation | `Leads-leadsCollected` > 0 |
| Integrations | `Leads-integrations` is not empty, `None` or `0` |

Full definitions come from the `analytics-feature-report` skill. The BIK dashboard has no catalogue; the 11 BIK signals and the columns behind them are in `BIK_SIGNALS` in `scripts/shortlist.py`.
