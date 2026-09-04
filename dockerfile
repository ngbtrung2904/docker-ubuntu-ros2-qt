# Use Ubuntu 24.04 LTS as the base
FROM ubuntu:24.04

# Prevent interactive timezone/keyboard prompts
ENV DEBIAN_FRONTEND=noninteractive

# 1. Install prerequisites and set-up locales
RUN apt-get update && apt-get install -y \
    locales \
    curl \
    gnupg2 \
    lsb-release \
    software-properties-common \
    && add-apt-repository universe \
    && locale-gen en_US.UTF-8 \
    && update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=en_US.UTF-8

# 2. Add ROS 2 repository for ROS Rolling
COPY mlib3rd/rolling.tar.xz /tmp/
RUN cd / \
    && tar xJf /tmp/rolling.tar.xz \
    && rm -f /tmp/rolling.tar.xz

# 3. Install requested packages and tools
RUN apt-get update && \ 
    apt-get -q -y install \
    build-essential \
    nano \
    sudo \
    cmake \
    gedit \
    git-all \
    openssh-server \
    terminator \
    ninja-build \
    libgoogle-glog-dev \
    clang \
    freeglut3-dev \
    gdal-bin \
    libgdal-dev \
    htop \
    nmap \
    net-tools \
    protobuf-compiler \
    libprotobuf-dev \
    mold \
    npm \
    libzmq3-dev \
    cppzmq-dev \
    sqlite3 \
    libsqlite3-dev \
    libxcb-cursor0 \
    libftgl-dev \
    python3 python3-pip python3-venv python3-dev \
    ccache \
    unzip \
    libusb-1.0-0-dev \
    fontconfig \
    netcat-openbsd \
    vlc \
    liblttng-ust-dev=2.13.7-1.1ubuntu2 \
    liblttng-ust1t64=2.13.7-1.1ubuntu2 \
    liblttng-ust-common1t64=2.13.7-1.1ubuntu2 \
    liblttng-ust-ctl5t64=2.13.7-1.1ubuntu2 \
    libconsole-bridge1.0 \
    libspdlog1.12 \
    libyaml-cpp0.8 \
    libtinyxml2-10 \
    liborocos-kdl1.5 \
    libopencv-core406t64 \
    libopencv-imgproc406t64 \
    libopencv-imgcodecs406t64 \
    libopencv-highgui406t64 \
    libopencv-videoio406t64 \
    python3-numpy \
    python3-lark \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages -U colcon-common-extensions

# 4. Copy and build Boost 1.83.0 from tarball
COPY mlib3rd/boost_1_83_0.tar.gz /tmp/
RUN cd /tmp \
    && tar -xzf boost_1_83_0.tar.gz \
    && cd boost_1_83_0 \
    && ./bootstrap.sh --prefix=/usr/local \
    && sudo ./b2 install \
    && cd / \
    && rm -rf /tmp/boost_1_83_0.tar.gz /tmp/boost_1_83_0

# 5. Copy and install fonts (JetBrains Mono & Roboto Condensed)
COPY mlib3rd/JetBrains_Mono-and-Roboto_Condensed.zip /tmp/
RUN mkdir -p /usr/local/share/fonts \
    && unzip -q /tmp/JetBrains_Mono-and-Roboto_Condensed.zip -d /tmp/fonts_temp \
    && find /tmp/fonts_temp -name "*.ttf" -exec cp {} /usr/local/share/fonts/ \; \
    && fc-cache -f -v \
    && rm -rf /tmp/JetBrains_Mono-and-Roboto_Condensed.zip /tmp/fonts_temp

# 6. Copy libsnap7.so into /usr/bin of container
COPY mlib3rd/snap7/libsnap7.so /usr/bin/

# 7. Copy, build, and install rtl-sdr, and add blacklist configuration
COPY mlib3rd/rtl-sdr.tar.xz /tmp/
RUN cd /tmp \
    && tar -xf rtl-sdr.tar.xz \
    && cd rtl-sdr \
    && mkdir -p build \
    && cd build \
    && cmake ../ -DINSTALL_UDEV_RULES=ON \
    && make \
    && make install \
    && ldconfig \
    && mkdir -p /etc/udev/rules.d \
    && cp ../rtl-sdr.rules /etc/udev/rules.d/ \
    && mkdir -p /etc/modprobe.d \
    && echo "blacklist dvb_usb_rtl28xxu" > /etc/modprobe.d/blacklist-rtl.conf \
    && cd / \
    && rm -rf /tmp/rtl-sdr.tar.xz /tmp/rtl-sdr

# 8. Copy and extract digital-map.zip into /opt
COPY mlib3rd/digital-map.zip /tmp/
RUN unzip -q /tmp/digital-map.zip -d /opt/ \
    && rm -f /tmp/digital-map.zip

RUN echo "source /opt/ros/rolling/setup.bash" >> /root/.bashrc

ADD mlib3rd/Qt.tar.gz /opt/

# 9. Copy and extract xf4_asr_rdd project source
COPY mlib3rd/xf4_asr_rdd.tar.gz /tmp/
RUN tar -xzf /tmp/xf4_asr_rdd.tar.gz -C /opt \
    && rm -f /tmp/xf4_asr_rdd.tar.gz

ENV PATH="/opt/Qt/Tools/QtCreator/bin:/opt/Qt/6.7.2/gcc_64/bin:${PATH}"
ENV CMAKE_PREFIX_PATH="/opt/Qt/6.7.2/gcc_64"
