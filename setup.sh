#/bin/bash
git clone https://github.com/QuintinShaw/openasr.git
cd openasr
git submodule update --init --recursive
cargo build --release -p openasr-cli --features hip

