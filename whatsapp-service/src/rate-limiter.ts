import pino from "pino";

const logger = pino({ name: "rate-limiter" });

interface RateLimiterConfig {
  maxPerMinute: number;    // default 8
  maxPerHour: number;      // default 200
  maxPerDay: number;       // default 1500
  burstAllowance: number;  // first N msgs can skip extra delay
  burstWindowMs: number;   // burst resets after this inactivity
  typingDelayPerCharMs: number; // simulate typing ~30ms/char
  maxTypingDelayMs: number;     // cap typing delay
  newChatPenaltyMs: number;     // extra delay for first msg to unknown chat
  baseDelayMs: number;          // minimum gap between messages
}

interface MessageRecord {
  timestamp: number;
  content: string;
}

const DEFAULT_CONFIG: RateLimiterConfig = {
  maxPerMinute: 8,
  maxPerHour: 200,
  maxPerDay: 1500,
  burstAllowance: 3,
  burstWindowMs: 30_000,
  typingDelayPerCharMs: 30,
  maxTypingDelayMs: 3_000,
  newChatPenaltyMs: 2_000,
  baseDelayMs: 1_500,
};

/**
 * Gaussian random using Box-Muller transform.
 * Returns value centered around `mean` with `stddev`.
 */
function gaussianRandom(mean: number, stddev: number): number {
  const u1 = Math.random();
  const u2 = Math.random();
  const z = Math.sqrt(-2 * Math.log(u1 || 0.0001)) * Math.cos(2 * Math.PI * u2);
  return Math.max(0, mean + z * stddev);
}

export class RateLimiter {
  private config: RateLimiterConfig;
  private messages: MessageRecord[] = [];
  private knownChats = new Set<string>();
  private burstCount = 0;
  private lastMessageTime = 0;

  // Stats
  private totalAllowed = 0;
  private totalDelayMs = 0;
  private totalBlocked = 0;

  constructor(config: Partial<RateLimiterConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Register a chat as "known" (has previous messages).
   */
  registerChat(chatId: string): void {
    this.knownChats.add(chatId);
  }

  /**
   * Calculate required delay before sending a message.
   * Returns { allowed: boolean, delayMs: number, reason?: string }.
   */
  check(chatId: string, content: string): { allowed: boolean; delayMs: number; reason?: string } {
    const now = Date.now();
    this.cleanup(now);

    // Check daily limit
    const oneDayAgo = now - 86_400_000;
    const dayCount = this.messages.filter(m => m.timestamp > oneDayAgo).length;
    if (dayCount >= this.config.maxPerDay) {
      this.totalBlocked++;
      return { allowed: false, delayMs: 0, reason: "daily_limit" };
    }

    // Check hourly limit
    const oneHourAgo = now - 3_600_000;
    const hourCount = this.messages.filter(m => m.timestamp > oneHourAgo).length;
    if (hourCount >= this.config.maxPerHour) {
      this.totalBlocked++;
      return { allowed: false, delayMs: 0, reason: "hourly_limit" };
    }

    // Check per-minute limit
    const oneMinAgo = now - 60_000;
    const minCount = this.messages.filter(m => m.timestamp > oneMinAgo).length;
    if (minCount >= this.config.maxPerMinute) {
      this.totalBlocked++;
      return { allowed: false, delayMs: 0, reason: "minute_limit" };
    }

    // Check for identical messages (anti-bot detection)
    const recentIdentical = this.messages.filter(
      m => m.timestamp > oneHourAgo && m.content === content
    ).length;
    if (recentIdentical >= 3) {
      this.totalBlocked++;
      return { allowed: false, delayMs: 0, reason: "identical_messages" };
    }

    // Calculate delay
    let delayMs = 0;

    // Burst handling: reset burst if inactive
    if (now - this.lastMessageTime > this.config.burstWindowMs) {
      this.burstCount = 0;
    }

    if (this.burstCount >= this.config.burstAllowance) {
      // Base delay with gaussian jitter (human-like variation)
      delayMs = gaussianRandom(this.config.baseDelayMs, this.config.baseDelayMs * 0.3);
    }

    // Typing simulation delay based on content length
    const typingDelay = Math.min(
      content.length * this.config.typingDelayPerCharMs,
      this.config.maxTypingDelayMs
    );
    delayMs += typingDelay;

    // New chat penalty
    if (!this.knownChats.has(chatId)) {
      delayMs += gaussianRandom(this.config.newChatPenaltyMs, 500);
    }

    this.totalAllowed++;
    this.totalDelayMs += delayMs;

    return { allowed: true, delayMs: Math.round(delayMs) };
  }

  /**
   * Record a sent message.
   */
  record(chatId: string, content: string): void {
    const now = Date.now();
    this.messages.push({ timestamp: now, content });
    this.knownChats.add(chatId);
    this.burstCount++;
    this.lastMessageTime = now;
  }

  /**
   * Remove messages older than 24h from tracking.
   */
  private cleanup(now: number): void {
    const cutoff = now - 86_400_000;
    this.messages = this.messages.filter(m => m.timestamp > cutoff);
  }

  /**
   * Get current stats.
   */
  getStats(): {
    messagesLastMinute: number;
    messagesLastHour: number;
    messagesLastDay: number;
    totalAllowed: number;
    totalBlocked: number;
    avgDelayMs: number;
    knownChats: number;
  } {
    const now = Date.now();
    return {
      messagesLastMinute: this.messages.filter(m => m.timestamp > now - 60_000).length,
      messagesLastHour: this.messages.filter(m => m.timestamp > now - 3_600_000).length,
      messagesLastDay: this.messages.length,
      totalAllowed: this.totalAllowed,
      totalBlocked: this.totalBlocked,
      avgDelayMs: this.totalAllowed > 0 ? Math.round(this.totalDelayMs / this.totalAllowed) : 0,
      knownChats: this.knownChats.size,
    };
  }

  /**
   * Reset all stats and state.
   */
  reset(): void {
    this.messages = [];
    this.totalAllowed = 0;
    this.totalBlocked = 0;
    this.totalDelayMs = 0;
    this.burstCount = 0;
    this.lastMessageTime = 0;
  }
}
