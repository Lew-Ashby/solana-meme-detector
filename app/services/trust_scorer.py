import logging
from app.models.schemas import RiskLevel, RiskFactors

logger = logging.getLogger(__name__)


class TrustScorer:
    WEIGHTS = {
        "mint_authority": 25,
        "freeze_authority": 20,
        "lp_locked": 25,
        "holder_concentration": 20,
        "token_age": 10
    }

    def calculate_trust_score(
        self,
        mint_authority_enabled: bool,
        freeze_authority_enabled: bool,
        lp_locked_percent: float,
        top_10_holder_percent: float,
        age_hours: float
    ) -> tuple[int, RiskLevel, RiskFactors]:

        scores = {}

        if mint_authority_enabled:
            scores["mint_authority"] = 0
        else:
            scores["mint_authority"] = 100

        if freeze_authority_enabled:
            scores["freeze_authority"] = 0
        else:
            scores["freeze_authority"] = 100

        if lp_locked_percent >= 90:
            scores["lp_locked"] = 100
        elif lp_locked_percent >= 80:
            scores["lp_locked"] = 85
        elif lp_locked_percent >= 60:
            scores["lp_locked"] = 60
        elif lp_locked_percent >= 40:
            scores["lp_locked"] = 40
        elif lp_locked_percent >= 20:
            scores["lp_locked"] = 20
        else:
            scores["lp_locked"] = 0

        if top_10_holder_percent <= 20:
            scores["holder_concentration"] = 100
        elif top_10_holder_percent <= 30:
            scores["holder_concentration"] = 80
        elif top_10_holder_percent <= 40:
            scores["holder_concentration"] = 60
        elif top_10_holder_percent <= 50:
            scores["holder_concentration"] = 40
        elif top_10_holder_percent <= 70:
            scores["holder_concentration"] = 20
        else:
            scores["holder_concentration"] = 0

        if age_hours >= 168:
            scores["token_age"] = 100
        elif age_hours >= 72:
            scores["token_age"] = 80
        elif age_hours >= 24:
            scores["token_age"] = 60
        elif age_hours >= 6:
            scores["token_age"] = 40
        elif age_hours >= 1:
            scores["token_age"] = 20
        else:
            scores["token_age"] = 0

        total_score = 0
        for factor, weight in self.WEIGHTS.items():
            factor_score = scores.get(factor, 0)
            weighted = (factor_score * weight) / 100
            total_score += weighted

        trust_score = int(round(total_score))

        if trust_score >= 80:
            risk_level = RiskLevel.SAFE
        elif trust_score >= 60:
            risk_level = RiskLevel.LOW
        elif trust_score >= 40:
            risk_level = RiskLevel.MEDIUM
        elif trust_score >= 20:
            risk_level = RiskLevel.HIGH
        else:
            risk_level = RiskLevel.EXTREME

        risk_factors = RiskFactors(
            mint_authority_enabled=mint_authority_enabled,
            freeze_authority_enabled=freeze_authority_enabled,
            lp_locked_percent=round(lp_locked_percent, 2),
            top_10_holder_percent=round(top_10_holder_percent, 2),
            age_hours=round(age_hours, 2)
        )

        logger.info(
            f"Trust score: {trust_score} ({risk_level.value}) - "
            f"mint={mint_authority_enabled}, freeze={freeze_authority_enabled}, "
            f"lp_locked={lp_locked_percent:.1f}%, top10={top_10_holder_percent:.1f}%, "
            f"age={age_hours:.1f}h"
        )

        return trust_score, risk_level, risk_factors


trust_scorer = TrustScorer()
