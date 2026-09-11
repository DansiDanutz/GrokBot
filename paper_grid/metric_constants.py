"""Shared bounds and vocabularies for observed paper lifecycle metrics."""
import re

ARMS = ('baseline', 'liquidation_filter')
CLOSE_REASONS = ('net_profit_target', 'position_loss_limit', 'price_stop',
                 'daily_loss_limit', 'rotation', 'unknown')
SYMBOL = re.compile(r'[A-Z0-9]{1,24}USDTM\Z')
SECONDS_PER_HOUR = 3600
MAX_REPORTED_TRADES = 200
MAX_REPORTED_SYMBOLS = 100
EXCURSION_FIELDS = ('observed_mae_net_usdt', 'observed_mfe_net_usdt',
                    'excursion_samples', 'excursion_missing_samples')
COHORT_NOTE = ('Recorded baseline lifecycle net PnL whose initial entry failed the '
               'contemporaneous CoinGlass filter; descriptive, not causal savings.')
EXCURSION_NOTE = ('Observed lifetime net-USDT marks include modeled exit slippage, '
                  'entry/exit fees and prior-rate funding; no intratick extrema are known.')

DEFAULT_TICK_SECONDS = 300
EVIDENCE_MAX_BYTES = 200 * 1024 * 1024
EVIDENCE_MAX_DAYS = 400
SECONDS_PER_DAY = 86400
