use std::cmp::min;
use std::time::Duration;

#[derive(Debug)]
pub struct ExponentialBackoff {
    initial: Duration,
    maximum: Duration,
    next: Duration,
}

impl ExponentialBackoff {
    pub fn new(initial: Duration, maximum: Duration) -> Self {
        Self {
            initial,
            maximum,
            next: initial,
        }
    }

    pub fn next_delay(&mut self) -> Duration {
        let current = self.next;
        self.next = min(self.next.saturating_mul(2), self.maximum);
        current
    }

    pub fn reset(&mut self) {
        self.next = self.initial;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn doubles_until_maximum_and_resets() {
        let mut backoff =
            ExponentialBackoff::new(Duration::from_millis(250), Duration::from_secs(1));

        assert_eq!(backoff.next_delay(), Duration::from_millis(250));
        assert_eq!(backoff.next_delay(), Duration::from_millis(500));
        assert_eq!(backoff.next_delay(), Duration::from_secs(1));
        assert_eq!(backoff.next_delay(), Duration::from_secs(1));

        backoff.reset();
        assert_eq!(backoff.next_delay(), Duration::from_millis(250));
    }
}
