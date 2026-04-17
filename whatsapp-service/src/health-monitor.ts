import pino from "pino";

const logger = pino({ name: "health-monitor" });

export type RiskLevel = "low" | "medium" | "high" | "critical";

interface HealthEvent {
  type: string;
  score: number;
  timestamp: number;
  detail?: string;
}

interface HealthMonitorConfig {
  /** Risk score thresholds */
  mediumThreshold: number;   // default 30
  highThreshold: number;     // default 60
  criticalThreshold: number; // default 85
  /** Auto-pause at this risk level */
  autoPauseLevel: RiskLevel;
  /** Window for tracking events (ms) */
  windowMs: number;          // default 6 hours
  /** Disconnect count thresholds per hour */
  disconnectWarningPerHour: number;  // default 3
  disconnectCriticalPerHour: number; // default 5
  /** Failed messages threshold per hour */
  failedMsgThresholdPerHour: number; // default 5
}

const DEFAULT_CONFIG: HealthMonitorConfig = {
  mediumThreshold: 30,
  highThreshold: 60,
  criticalThreshold: 85,
  autoPauseLevel: "high",
  windowMs: 6 * 3_600_000,
  disconnectWarningPerHour: 3,
  disconnectCriticalPerHour: 5,
  failedMsgThresholdPerHour: 5,
};

const EVENT_SCORES: Record<string, number> = {
  forbidden_403: 40,
  logged_out_401: 60,
  timelock_463: 25,
  disconnect_critical: 30,
  disconnect_warning: 15,
  failed_messages: 20,
};

export class HealthMonitor {
  private config: HealthMonitorConfig;
  private events: HealthEvent[] = [];
  private disconnects: number[] = [];
  private failedMessages: number[] = [];
  private paused = false;
  private onRiskChange?: (level: RiskLevel, score: number) => void;

  constructor(
    config: Partial<HealthMonitorConfig> = {},
    onRiskChange?: (level: RiskLevel, score: number) => void,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.onRiskChange = onRiskChange;
  }

  /**
   * Track a connection error from Baileys.
   */
  trackError(statusCode: number, message: string): void {
    if (statusCode === 403) {
      this.addEvent("forbidden_403", `403 Forbidden: ${message}`);
    } else if (statusCode === 401) {
      this.addEvent("logged_out_401", `401 Logged out: ${message}`);
    } else if (statusCode === 463) {
      this.addEvent("timelock_463", `463 Timelock: ${message}`);
    }
  }

  /**
   * Track a disconnection event.
   */
  trackDisconnect(): void {
    const now = Date.now();
    this.disconnects.push(now);
    this.checkDisconnectRate();
  }

  /**
   * Track a failed message send.
   */
  trackFailedMessage(): void {
    const now = Date.now();
    this.failedMessages.push(now);
    this.checkFailedMessageRate();
  }

  /**
   * Check disconnect rate and add events if thresholds exceeded.
   */
  private checkDisconnectRate(): void {
    const oneHourAgo = Date.now() - 3_600_000;
    this.disconnects = this.disconnects.filter(t => t > oneHourAgo);
    const count = this.disconnects.length;

    if (count >= this.config.disconnectCriticalPerHour) {
      this.addEvent("disconnect_critical", `${count} disconnects in last hour`);
    } else if (count >= this.config.disconnectWarningPerHour) {
      this.addEvent("disconnect_warning", `${count} disconnects in last hour`);
    }
  }

  /**
   * Check failed message rate.
   */
  private checkFailedMessageRate(): void {
    const oneHourAgo = Date.now() - 3_600_000;
    this.failedMessages = this.failedMessages.filter(t => t > oneHourAgo);
    const count = this.failedMessages.length;

    if (count >= this.config.failedMsgThresholdPerHour) {
      this.addEvent("failed_messages", `${count} failed messages in last hour`);
    }
  }

  /**
   * Add a health event and check risk level.
   */
  private addEvent(type: string, detail?: string): void {
    const score = EVENT_SCORES[type] || 10;
    this.events.push({ type, score, timestamp: Date.now(), detail });
    this.cleanup();

    const riskLevel = this.getRiskLevel();
    const totalScore = this.getScore();
    logger.warn({ type, detail, riskLevel, totalScore }, "health event recorded");

    if (this.onRiskChange) {
      this.onRiskChange(riskLevel, totalScore);
    }

    // Auto-pause check
    const levels: RiskLevel[] = ["low", "medium", "high", "critical"];
    const autoPauseIdx = levels.indexOf(this.config.autoPauseLevel);
    const currentIdx = levels.indexOf(riskLevel);
    if (currentIdx >= autoPauseIdx && !this.paused) {
      this.paused = true;
      logger.error({ riskLevel, totalScore }, "AUTO-PAUSE: messaging paused due to high risk");
    }
  }

  /**
   * Get total risk score from events in the window.
   */
  getScore(): number {
    this.cleanup();
    return this.events.reduce((sum, e) => sum + e.score, 0);
  }

  /**
   * Get current risk level.
   */
  getRiskLevel(): RiskLevel {
    const score = this.getScore();
    if (score >= this.config.criticalThreshold) return "critical";
    if (score >= this.config.highThreshold) return "high";
    if (score >= this.config.mediumThreshold) return "medium";
    return "low";
  }

  /**
   * Whether sending is paused due to high risk.
   */
  isPaused(): boolean {
    return this.paused;
  }

  /**
   * Resume sending after manual review.
   */
  resume(): void {
    this.paused = false;
    logger.info("health monitor resumed manually");
  }

  /**
   * Get full health report.
   */
  getReport(): {
    riskLevel: RiskLevel;
    riskScore: number;
    paused: boolean;
    events: { type: string; detail?: string; timestamp: number }[];
    disconnectsLastHour: number;
    failedMessagesLastHour: number;
  } {
    this.cleanup();
    const oneHourAgo = Date.now() - 3_600_000;

    return {
      riskLevel: this.getRiskLevel(),
      riskScore: this.getScore(),
      paused: this.paused,
      events: this.events.map(e => ({
        type: e.type,
        detail: e.detail,
        timestamp: e.timestamp,
      })),
      disconnectsLastHour: this.disconnects.filter(t => t > oneHourAgo).length,
      failedMessagesLastHour: this.failedMessages.filter(t => t > oneHourAgo).length,
    };
  }

  /**
   * Remove events outside the tracking window.
   */
  private cleanup(): void {
    const cutoff = Date.now() - this.config.windowMs;
    this.events = this.events.filter(e => e.timestamp > cutoff);
    this.disconnects = this.disconnects.filter(t => t > cutoff);
    this.failedMessages = this.failedMessages.filter(t => t > cutoff);
  }

  /**
   * Reset all state.
   */
  reset(): void {
    this.events = [];
    this.disconnects = [];
    this.failedMessages = [];
    this.paused = false;
  }
}
