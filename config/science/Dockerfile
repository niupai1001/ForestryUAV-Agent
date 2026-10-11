FROM public.ecr.aws/docker/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
# Developer build context: only the verified micromamba binary and explicit lock.
# micromamba 2.9.0-0 SHA256:
# 366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3
COPY --chmod=755 micromamba-linux-64 /usr/local/bin/micromamba
COPY conda-linux-64.lock.txt /opt/science-input.lock.txt
ENV MAMBA_ROOT_PREFIX=/opt/mamba
RUN printf '@EXPLICIT\n' > /opt/science-install.lock.txt \
    && sed -n '/^https:/p' /opt/science-input.lock.txt >> /opt/science-install.lock.txt \
    && micromamba create --yes --prefix /opt/science --file /opt/science-install.lock.txt \
    && micromamba list --prefix /opt/science --explicit --md5 > /opt/science/explicit-lock.txt \
    && micromamba clean --all --yes
ENV PATH=/opt/science/bin:$PATH
ENV PROJ_NETWORK=OFF OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 GDAL_NUM_THREADS=4 R_USER=/tmp
ENV PROJ_DATA=/opt/science/share/proj PROJ_LIB=/opt/science/share/proj GDAL_DATA=/opt/science/share/gdal
