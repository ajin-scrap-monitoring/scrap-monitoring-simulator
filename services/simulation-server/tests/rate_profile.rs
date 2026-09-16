use scrap_monitoring_simulation_server::{
    randomness::{ModelRng, RandomSource, StreamScope},
    rate_profile::{
        MAX_RATE_SEGMENTS, RateProfileError, SmoothRateProfile, SmoothRateSegment,
        create_smooth_rate_profile,
    },
};

fn segment(start: f64, duration: f64, deviation: f64) -> SmoothRateSegment {
    SmoothRateSegment::new(start, duration, deviation).unwrap()
}

fn close(actual: f64, expected: f64) {
    assert!(
        (actual - expected).abs() <= 1e-12 * expected.abs().max(1.0),
        "{actual} != {expected}"
    );
}

fn rng() -> ModelRng {
    ModelRng::new(123_456_789, StreamScope::Global, "fill-rate-profile").unwrap()
}

struct CountingWords {
    value: u64,
    calls: usize,
}

impl RandomSource for CountingWords {
    fn next_u64(&mut self) -> u64 {
        self.calls += 1;
        self.value
    }
}

#[test]
fn segment_and_profile_math_match_model_v1_values() {
    let profile = SmoothRateProfile::new(
        11.0,
        vec![
            segment(0.0, 2.0, 0.5),
            segment(2.0, 4.0, -0.25),
            segment(6.0, 2.0, -0.25),
            segment(8.0, 2.0, 0.25),
        ],
    )
    .unwrap();
    for (elapsed, factor, integral) in [
        (0.0, 1.0, 0.0),
        (0.5, 1.25, 0.545_422_528_454_052_4),
        (1.0, 1.5, 1.25),
        (2.0, 1.0, 2.5),
        (3.0, 0.875, 3.454_577_471_545_947_6),
        (4.0, 0.75, 4.25),
        (6.0, 1.0, 6.0),
        (7.0, 0.75, 6.875),
        (8.0, 1.0, 7.75),
        (9.0, 1.25, 8.875),
        (10.5, 1.0, 10.5),
        (11.0, 1.0, 11.0),
    ] {
        close(profile.factor_at(elapsed).unwrap(), factor);
        close(
            profile.integrated_factor_between(0.0, elapsed).unwrap(),
            integral,
        );
    }
    close(
        profile.integrated_factor_between(0.5, 1.5).unwrap(),
        1.409_154_943_091_895_3,
    );
    close(
        profile.integrated_factor_between(3.0, 7.0).unwrap(),
        3.420_422_528_454_052_4,
    );
    assert_eq!(
        profile
            .integrated_factor_between(0.0, 11.0)
            .unwrap()
            .to_bits(),
        11.0f64.to_bits()
    );
    assert_eq!(profile.integrated_factor_between(4.0, 4.0).unwrap(), 0.0);
    let lobe = profile.segments()[0];
    assert_eq!(lobe.factor_at(-1.0), 1.0);
    assert_eq!(lobe.factor_at(2.0), 1.0);
    assert_eq!(lobe.integrated_deviation_to(-1.0), 0.0);
    close(lobe.integrated_deviation_to(3.0), 0.5);
}

#[test]
fn internal_gaps_and_the_unfilled_tail_have_unit_factor() {
    let profile =
        SmoothRateProfile::new(10.0, vec![segment(1.0, 2.0, 0.5), segment(5.0, 4.0, -0.25)])
            .unwrap();
    for elapsed in [0.0, 1.0, 3.0, 4.0, 5.0, 9.0, 10.0] {
        assert_eq!(profile.factor_at(elapsed).unwrap(), 1.0);
    }
    close(profile.integrated_factor_between(0.0, 4.0).unwrap(), 4.5);
}

#[test]
fn generated_profiles_are_bounded_balanced_and_reproducible() {
    let profile = create_smooth_rate_profile(37.0, [0.4, 1.8], [2.0, 4.0], &mut rng()).unwrap();
    assert_eq!(
        profile,
        create_smooth_rate_profile(37.0, [0.4, 1.8], [2.0, 4.0], &mut rng()).unwrap()
    );
    assert_eq!(profile.segments().len() % 2, 0);
    for pair in profile.segments().as_chunks::<2>().0 {
        close(
            pair[0].duration_s() * pair[0].deviation() + pair[1].duration_s() * pair[1].deviation(),
            0.0,
        );
    }
    let factors: Vec<_> = (0..=1000)
        .map(|index| profile.factor_at(37.0 * f64::from(index) / 1000.0).unwrap())
        .collect();
    assert!(factors.iter().all(|factor| (0.4..=1.8).contains(factor)));
    assert!(factors.iter().any(|factor| *factor != 1.0));
    assert_eq!(profile.integrated_factor_between(0.0, 37.0).unwrap(), 37.0);
    let boundaries = [0.0, 1.25, 4.75, 11.0, 19.5, 37.0];
    let integral: f64 = boundaries
        .windows(2)
        .map(|pair| profile.integrated_factor_between(pair[0], pair[1]).unwrap())
        .sum();
    close(integral, 37.0);
}

#[test]
fn version_one_profile_matches_exact_segment_bits_and_draw_count() {
    let mut source = rng();
    let profile = create_smooth_rate_profile(11.0, [0.4, 1.8], [2.0, 4.0], &mut source).unwrap();
    let expected = [
        [0x0000000000000000, 0x40067c02da160255, 0x3fda618a620c4076],
        [0x40067c02da160255, 0x4005f6ee4def8a98, 0xbfdb016122b2492a],
        [0x401639789402c676, 0x4004efe02b16322e, 0xbfdf54ce476a39b5],
        [0x402058b454c6efc6, 0x40033d0b835f167d, 0x3fe10c7ae862e5b6],
    ];
    let actual: Vec<_> = profile
        .segments()
        .iter()
        .map(|segment| {
            [
                segment.start_s().to_bits(),
                segment.duration_s().to_bits(),
                segment.deviation().to_bits(),
            ]
        })
        .collect();
    assert_eq!(actual, expected);
    let mut independently_advanced = rng();
    for _ in 0..8 {
        independently_advanced.next_u64();
    }
    assert_eq!(source.next_u64(), independently_advanced.next_u64());
    assert_eq!(profile.integrated_factor_between(0.0, 11.0).unwrap(), 11.0);
}

#[test]
fn generation_consumes_four_words_per_pair_even_for_fixed_durations() {
    let mut rng = CountingWords { value: 0, calls: 0 };
    let empty =
        create_smooth_rate_profile(4.0f64.next_down(), [0.4, 1.8], [2.0, 4.0], &mut rng).unwrap();
    assert!(empty.segments().is_empty());
    assert_eq!(rng.calls, 0);
    let pair = create_smooth_rate_profile(4.0, [0.4, 1.8], [2.0, 4.0], &mut rng).unwrap();
    assert_eq!(pair.segments().len(), 2);
    assert_eq!(rng.calls, 4);
    for range in [[1.0, 1.8], [0.4, 1.0], [1.0, 1.0]] {
        let flat = create_smooth_rate_profile(10.0, range, [1.0, 2.0], &mut rng).unwrap();
        assert!(flat.segments().is_empty());
        assert_eq!(flat.factor_at(5.0).unwrap(), 1.0);
        assert_eq!(flat.integrated_factor_between(2.0, 7.0).unwrap(), 5.0);
    }
    assert_eq!(rng.calls, 4);
}

#[test]
fn long_profiles_preserve_internal_rounding_tolerance_and_exact_cycle_integral() {
    let profile =
        create_smooth_rate_profile(86_400.0, [0.5, 1.5], [60.0, 240.0], &mut rng()).unwrap();
    assert!(profile.segments().len() > 100);
    assert!(profile.segments().len() < MAX_RATE_SEGMENTS);
    assert_eq!(
        profile
            .integrated_factor_between(0.0, profile.duration_s())
            .unwrap(),
        profile.duration_s()
    );
}

#[test]
fn profile_tolerance_is_duration_scaled_but_does_not_relax_query_bounds() {
    for (delta, accepted) in [(5e-12, true), (2e-11, false)] {
        assert_eq!(
            SmoothRateProfile::new(
                10.0,
                vec![segment(0.0, 2.0, 0.0), segment(2.0 - delta, 2.0, 0.0)]
            )
            .is_ok(),
            accepted
        );
        assert_eq!(
            SmoothRateProfile::new(10.0, vec![segment(8.0, 2.0 + delta, 0.0)]).is_ok(),
            accepted
        );
        assert_eq!(
            SmoothRateProfile::new(10.0, vec![segment(0.0, 2.0, delta)]).is_ok(),
            accepted
        );
    }
    let tolerated = SmoothRateProfile::new(10.0, vec![segment(0.0, 2.0, 5e-12)]).unwrap();
    assert_eq!(
        tolerated.integrated_factor_between(0.0, 10.0).unwrap(),
        10.0
    );
    for invalid in [
        f64::NAN,
        f64::INFINITY,
        -f64::MIN_POSITIVE,
        10.0f64.next_up(),
    ] {
        assert_eq!(
            tolerated.factor_at(invalid),
            Err(RateProfileError::InvalidElapsed)
        );
        assert_eq!(
            tolerated.integrated_factor_between(0.0, invalid),
            Err(RateProfileError::InvalidElapsed)
        );
    }
    assert_eq!(
        tolerated.integrated_factor_between(2.0, 1.0),
        Err(RateProfileError::ReversedInterval)
    );
}

#[test]
fn tolerance_does_not_allow_unordered_binary_search_boundaries() {
    for segments in [
        vec![segment(4e-13, 2e-13, 0.5), segment(1e-13, 2e-13, -0.5)],
        vec![segment(0.0, 9e-13, 0.0), segment(2e-13, 2e-13, 0.0)],
    ] {
        assert_eq!(
            SmoothRateProfile::new(1.0, segments),
            Err(RateProfileError::UnorderedSegments)
        );
    }
}

#[test]
fn invalid_constructors_and_ranges_return_errors() {
    for start in [-1.0, f64::NAN, f64::INFINITY] {
        assert_eq!(
            SmoothRateSegment::new(start, 1.0, 0.0),
            Err(RateProfileError::InvalidSegmentStart)
        );
    }
    for duration in [-1.0, 0.0, f64::NAN, f64::INFINITY] {
        assert_eq!(
            SmoothRateSegment::new(0.0, duration, 0.0),
            Err(RateProfileError::InvalidSegmentDuration)
        );
        assert_eq!(
            SmoothRateProfile::new(duration, vec![]),
            Err(RateProfileError::InvalidDuration)
        );
    }
    for deviation in [-1.0, -2.0, f64::NAN, f64::INFINITY] {
        assert_eq!(
            SmoothRateSegment::new(0.0, 1.0, deviation),
            Err(RateProfileError::InvalidDeviation)
        );
    }
    for range in [
        [-0.1, 1.5],
        [0.0, 1.5],
        [1.1, 1.5],
        [0.5, 0.9],
        [2.0, 1.0],
        [f64::NAN, 1.5],
        [0.5, f64::INFINITY],
    ] {
        assert_eq!(
            create_smooth_rate_profile(10.0, range, [1.0, 2.0], &mut rng()),
            Err(RateProfileError::InvalidFactorRange)
        );
    }
    let mut untouched = CountingWords { value: 0, calls: 0 };
    assert_eq!(
        create_smooth_rate_profile(10.0, [0.0, 1.5], [1.0, 2.0], &mut untouched),
        Err(RateProfileError::InvalidFactorRange)
    );
    assert_eq!(untouched.calls, 0);
    for range in [
        [0.0, 2.0],
        [2.0, 1.0],
        [f64::NAN, 2.0],
        [1.0, f64::INFINITY],
    ] {
        assert_eq!(
            create_smooth_rate_profile(10.0, [0.5, 1.5], range, &mut rng()),
            Err(RateProfileError::InvalidChangeRange)
        );
    }
    assert_eq!(
        SmoothRateProfile::new(2.0, vec![segment(0.0, 2.0, 0.5)]),
        Err(RateProfileError::NonZeroIntegral)
    );
}

#[test]
fn segment_limits_and_non_progressing_float_arithmetic_are_bounded_errors() {
    assert_eq!(
        SmoothRateSegment::new(1e20, 1.0, 0.0),
        Err(RateProfileError::NoProgress)
    );
    assert_eq!(
        SmoothRateSegment::new(f64::MAX, f64::MAX, 0.0),
        Err(RateProfileError::NoProgress)
    );
    assert_eq!(
        SmoothRateProfile::new(1.0, vec![segment(0.0, 1.0, 0.0); MAX_RATE_SEGMENTS + 1]),
        Err(RateProfileError::SegmentLimit)
    );
    let mut boundary = CountingWords { value: 0, calls: 0 };
    let longest = create_smooth_rate_profile(
        MAX_RATE_SEGMENTS as f64,
        [0.5, 1.5],
        [1.0, 1.0],
        &mut boundary,
    )
    .unwrap();
    assert_eq!(longest.segments().len(), MAX_RATE_SEGMENTS);
    assert_eq!(boundary.calls, MAX_RATE_SEGMENTS * 2);
    let mut zero = CountingWords { value: 0, calls: 0 };
    assert_eq!(
        create_smooth_rate_profile(1_000_002.0, [0.5, 1.5], [1.0, 1.0], &mut zero),
        Err(RateProfileError::SegmentLimit)
    );
    assert_eq!(zero.calls, MAX_RATE_SEGMENTS * 2);
    struct Scripted {
        values: std::vec::IntoIter<u64>,
    }
    impl RandomSource for Scripted {
        fn next_u64(&mut self) -> u64 {
            self.values.next().unwrap()
        }
    }
    let mut stalled = Scripted {
        values: vec![1 << 63, 0, 0, 0].into_iter(),
    };
    assert_eq!(
        create_smooth_rate_profile(1e20, [0.5, 1.5], [1.0, 1e20], &mut stalled),
        Err(RateProfileError::NoProgress)
    );
}
