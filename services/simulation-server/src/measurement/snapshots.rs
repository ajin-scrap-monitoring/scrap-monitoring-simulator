//! Immutable surface history for scans that cross scenario update events.

use std::{collections::VecDeque, sync::Arc};

use crate::scenario::SurfaceSnapshot;

use super::{MeasurementError, Result};

#[derive(Clone, Debug)]
struct Entry {
    started_at_s: f64,
    snapshot: Arc<SurfaceSnapshot>,
}

#[derive(Clone, Debug)]
pub struct SnapshotEventCoordinator {
    entries: VecDeque<Entry>,
}

impl SnapshotEventCoordinator {
    pub fn new(initial: SurfaceSnapshot) -> Self {
        Self {
            entries: VecDeque::from([Entry {
                started_at_s: 0.0,
                snapshot: Arc::new(initial),
            }]),
        }
    }

    pub fn latest_event_elapsed_s(&self) -> f64 {
        self.entries
            .back()
            .expect("coordinator always retains one snapshot")
            .started_at_s
    }

    pub fn push_event(&mut self, elapsed_s: f64, snapshot: SurfaceSnapshot) -> Result<()> {
        if !elapsed_s.is_finite() || elapsed_s <= self.latest_event_elapsed_s() {
            return Err(MeasurementError::Invalid(
                "surface event times must increase and be finite",
            ));
        }
        if !snapshot.shares_grid_with(
            &self
                .entries
                .front()
                .expect("coordinator always retains one snapshot")
                .snapshot,
        ) {
            return Err(MeasurementError::Invalid(
                "surface event snapshots must share one static grid",
            ));
        }
        self.entries.push_back(Entry {
            started_at_s: elapsed_s,
            snapshot: Arc::new(snapshot),
        });
        Ok(())
    }

    pub fn snapshot_at(&self, elapsed_s: f64) -> Result<Arc<SurfaceSnapshot>> {
        if !elapsed_s.is_finite() || elapsed_s < 0.0 {
            return Err(MeasurementError::Invalid(
                "surface sample time must be finite and non-negative",
            ));
        }
        let entry = self
            .entries
            .iter()
            .rev()
            .find(|entry| entry.started_at_s <= elapsed_s)
            .ok_or(MeasurementError::Invalid(
                "surface history no longer covers the sample time",
            ))?;
        Ok(Arc::clone(&entry.snapshot))
    }

    pub fn discard_before_earliest_pending(&mut self, elapsed_s: f64) -> Result<()> {
        if !elapsed_s.is_finite() || elapsed_s < 0.0 {
            return Err(MeasurementError::Invalid(
                "surface retention time must be finite and non-negative",
            ));
        }
        while self.entries.len() > 1 && self.entries[1].started_at_s <= elapsed_s {
            self.entries.pop_front();
        }
        Ok(())
    }

    pub fn retained_snapshot_count(&self) -> usize {
        self.entries.len()
    }
}
