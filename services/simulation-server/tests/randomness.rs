use scrap_monitoring_simulation_server::randomness::{
    ModelRng, RandomError, RandomSource, StreamScope, derive_stream_seed,
};

struct Words {
    words: std::vec::IntoIter<u64>,
    calls: usize,
}

impl Words {
    fn new(words: impl IntoIterator<Item = u64>) -> Self {
        Self {
            words: words.into_iter().collect::<Vec<_>>().into_iter(),
            calls: 0,
        }
    }
}

impl RandomSource for Words {
    fn next_u64(&mut self) -> u64 {
        self.calls += 1;
        self.words.next().expect("unexpected random consumption")
    }
}

#[test]
fn unit_sampling_uses_only_the_top_53_bits_and_excludes_one() {
    let mut rng = Words::new([0, 2047, 2048, 1 << 63, u64::MAX]);
    assert_eq!(rng.unit_f64().to_bits(), 0.0f64.to_bits());
    assert_eq!(rng.unit_f64().to_bits(), 0.0f64.to_bits());
    assert_eq!(rng.unit_f64(), 1.0 / 9_007_199_254_740_992.0);
    assert_eq!(rng.unit_f64(), 0.5);
    assert_eq!(rng.unit_f64(), 1.0f64.next_down());
    assert_eq!(rng.calls, 5);
}

#[test]
fn boolean_sampling_uses_the_top_bit_and_a_whole_word() {
    let mut rng = Words::new([0, (1 << 63) - 1, 1 << 63, u64::MAX]);
    assert_eq!(
        (0..4).map(|_| rng.boolean()).collect::<Vec<_>>(),
        [false, false, true, true]
    );
    assert_eq!(rng.calls, 4);
}

#[test]
fn uniform_sampling_preserves_lower_and_excludes_a_rounded_upper_endpoint() {
    let mut rng = Words::new([0, u64::MAX, u64::MAX, 0, u64::MAX]);
    assert_eq!(rng.uniform(-2.0, 4.0).unwrap(), -2.0);
    assert!(rng.uniform(-2.0, 4.0).unwrap() < 4.0);
    assert_eq!(rng.uniform(1.0, 1.0f64.next_up()).unwrap(), 1.0);
    assert_eq!(
        rng.uniform(-0.0, 0.0).unwrap().to_bits(),
        (-0.0f64).to_bits()
    );
    assert_eq!(rng.uniform(7.0, 7.0).unwrap(), 7.0);
    assert_eq!(rng.calls, 5);
}

#[test]
fn invalid_uniform_ranges_do_not_advance_the_stream() {
    let mut rng = Words::new([]);
    for [lower, upper] in [
        [2.0, 1.0],
        [f64::NAN, 1.0],
        [0.0, f64::NAN],
        [f64::NEG_INFINITY, 1.0],
        [0.0, f64::INFINITY],
        [-f64::MAX, f64::MAX],
    ] {
        assert_eq!(rng.uniform(lower, upper), Err(RandomError::InvalidRange));
    }
    assert_eq!(rng.calls, 0);
}

#[test]
fn stream_domain_scope_and_utf8_lengths_prevent_concatenation_collisions() {
    let keys = [
        (StreamScope::Global, "quality"),
        (StreamScope::Sensor("lidar_1"), "quality"),
        (StreamScope::Sensor("lidar_2"), "quality"),
        (StreamScope::Sensor("lidar_1"), "distance-noise"),
        (StreamScope::Sensor("c"), "ab"),
        (StreamScope::Sensor("bc"), "a"),
        (StreamScope::Sensor("\0bc"), "a"),
        (StreamScope::Sensor("c"), "a\0b"),
        (StreamScope::Sensor("\u{b77c}\u{c774}\u{b2e4}"), "quality"),
    ];
    let seeds = keys.map(|(scope, domain)| derive_stream_seed(7, scope, domain).unwrap());
    for (index, seed) in seeds.iter().enumerate() {
        assert!(!seeds[..index].contains(seed));
    }
    assert_ne!(
        derive_stream_seed(0, StreamScope::Global, "quality").unwrap(),
        derive_stream_seed(u64::MAX, StreamScope::Global, "quality").unwrap()
    );
    assert_eq!(
        derive_stream_seed(0, StreamScope::Global, ""),
        Err(RandomError::EmptyDomain)
    );
    assert_eq!(
        derive_stream_seed(0, StreamScope::Sensor(""), "quality"),
        Err(RandomError::EmptySensor)
    );
}

#[test]
fn stream_creation_and_other_consumers_do_not_change_replay() {
    let mut baseline = ModelRng::new(123, StreamScope::Sensor("lidar_1"), "quality").unwrap();
    let mut replay = baseline.clone();
    let mut unrelated = ModelRng::new(123, StreamScope::Global, "surface").unwrap();
    for _ in 0..1000 {
        unrelated.next_u64();
        assert_eq!(baseline.next_u64(), replay.next_u64());
    }
    let mut fresh = ModelRng::new(123, StreamScope::Sensor("lidar_1"), "quality").unwrap();
    let mut same = ModelRng::new(123, StreamScope::Sensor("lidar_1"), "quality").unwrap();
    assert_eq!(fresh.next_u64(), same.next_u64());
}

#[test]
fn version_one_seed_layout_and_sampling_match_independent_exact_vectors() {
    struct Vector {
        root: u64,
        scope: StreamScope<'static>,
        domain: &'static str,
        layout: &'static str,
        digest: &'static str,
        words: [u64; 8],
        unit_bits: [u64; 4],
        booleans: [bool; 4],
    }
    let vectors = [
        Vector {
            root: 123_456_789,
            scope: StreamScope::Global,
            domain: "fill-rate-profile",
            layout: "73637261702d6c696461722d73696d000000000100000000075bcd15000000001166696c6c2d726174652d70726f66696c6500000000",
            digest: "b0a72ef4ca9a65bb4da996c37342d9d605d2d3639972eb40296a8ff94f3072b9",
            words: [
                0x67c02da160255152,
                0x5f6ee4def8a9785e,
                0x9dc978bf8905af52,
                0xb409323ca4926c33,
                0x6d6abb14e38f89f4,
                0x7d58dc46c5e9c0c4,
                0x27793364b100afe3,
                0xd0e009dc1980b522,
            ],
            unit_bits: [
                0x3fd9f00b68580954,
                0x3fd7dbb937be2a5e,
                0x3fe3b92f17f120b5,
                0x3fe681264794924d,
            ],
            booleans: [false, false, true, true],
        },
        Vector {
            root: u64::MAX,
            scope: StreamScope::Sensor("lidar_2"),
            domain: "distance-noise",
            layout: "73637261702d6c696461722d73696d0000000001ffffffffffffffff010000000e64697374616e63652d6e6f697365000000076c696461725f32",
            digest: "8415905061f76d6f54bd6a2ed218d2d017aed29e58ed21337ef198d4c5ecff74",
            words: [
                0xa5d51d772e874aeb,
                0x0a97c4a7baef51f9,
                0x51ade8a6fc576394,
                0xe262c9be7a31aa39,
                0x5d1d07187c26963b,
                0x6a7daa701d8cf409,
                0x1c9b265440d04763,
                0xa6795f6c41584ea7,
            ],
            unit_bits: [
                0x3fe4baa3aee5d0e9,
                0x3fa52f894f75dea0,
                0x3fd46b7a29bf15d8,
                0x3fec4c5937cf4635,
            ],
            booleans: [true, false, false, true],
        },
    ];
    for vector in vectors {
        use sha2::{Digest, Sha256};
        let decode = |hex: &str| -> Vec<u8> {
            hex.as_bytes()
                .as_chunks::<2>()
                .0
                .iter()
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect()
        };
        let expected = decode(vector.digest);
        assert_eq!(Sha256::digest(decode(vector.layout)).as_slice(), expected);
        assert_eq!(
            derive_stream_seed(vector.root, vector.scope, vector.domain)
                .unwrap()
                .as_slice(),
            expected
        );
        let fresh = || ModelRng::new(vector.root, vector.scope, vector.domain).unwrap();
        let mut words = fresh();
        assert_eq!(
            std::array::from_fn::<_, 8, _>(|_| words.next_u64()),
            vector.words
        );
        let mut units = fresh();
        assert_eq!(
            std::array::from_fn::<_, 4, _>(|_| units.unit_f64().to_bits()),
            vector.unit_bits
        );
        let mut booleans = fresh();
        assert_eq!(
            std::array::from_fn::<_, 4, _>(|_| booleans.boolean()),
            vector.booleans
        );
    }
}
