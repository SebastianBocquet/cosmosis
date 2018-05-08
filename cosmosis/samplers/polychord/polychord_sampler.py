#coding: utf-8
from __future__ import print_function
from builtins import str
from builtins import range
from .. import ParallelSampler
import ctypes as ct
import os
import cosmosis
import numpy as np
import sys

prior_type = ct.CFUNCTYPE(None, 
    ct.POINTER(ct.c_double),  #hypercube
    ct.POINTER(ct.c_double),  #physical
    ct.c_int,   #ndim
)

loglike_type = ct.CFUNCTYPE(ct.c_double, 
    ct.POINTER(ct.c_double),  #physical
    ct.c_int,   #ndim
    ct.POINTER(ct.c_double),  #derived
    ct.c_int,   #nderived
)


dumper_type = ct.CFUNCTYPE(None, #void
    ct.c_int,  #ndead
    ct.c_int,  #nlive
    ct.c_int,  #npars
    ct.POINTER(ct.c_double),   #live
    ct.POINTER(ct.c_double),   #dead
    ct.POINTER(ct.c_double),   #logweights
    ct.c_double,   #logZ
    ct.c_double,   #logZerr
)


polychord_args = [
    loglike_type, #loglike,
    prior_type,   #prior,
    dumper_type,  #dumper,
    ct.c_int,     #nlive
    ct.c_int,     #nrepeats
    ct.c_int,     #nprior
    ct.c_bool,    #do_clustering
    ct.c_int,     #feedback
    ct.c_double,  #precision_criterion
    ct.c_double,  #logzero
    ct.c_int,     #max_ndead
    ct.c_double,  #boost_posterior
    ct.c_bool,    #posteriors
    ct.c_bool,    #equals
    ct.c_bool,    #cluster_posteriors
    ct.c_bool,    #write_resume 
    ct.c_bool,    #write_paramnames
    ct.c_bool,    #read_resume
    ct.c_bool,    #write_stats
    ct.c_bool,    #write_live
    ct.c_bool,    #write_dead
    ct.c_bool,    #write_prior
    ct.c_double,  #compression_factor
    ct.c_int,     #nDims
    ct.c_int,     #nDerived 
    ct.c_char_p,  #base_dir
    ct.c_char_p,  #file_root
    ct.c_int,     #nGrade
    ct.c_double_p,#grade_frac
    ct.c_int_p,   #grade_dims
    ct.c_int,     #n_nlives
    ct.c_double_p,#loglikes
    ct.c_int_p,   #nlives
    ct.c_int,     #seed
]


POLYCHORD_SECTION='polychord'


class PolyChordSampler(ParallelSampler):
    parallel_output = False
    sampler_outputs = [("post", float), ("weight", float)]
    supports_smp=False

    def config(self):
        if self.pool:
            libname = "libchord_mpi.so"
        else:
            libname = "libchord.so"

        dirname = os.path.split(__file__)[0]
        libname = os.path.join(dirname, "polychord", libname)
            
        try:
            libchord = ct.cdll.LoadLibrary(libname)
        except Exception as error:
            sys.stderr.write("PolyChord could not be loaded.\n")
            sys.stderr.write("This may mean an MPI compiler was not found to compile it,\n")
            sys.stderr.write("or that some other error occurred.  More info below.\n")
            sys.stderr.write(str(error)+'\n')
            sys.exit(1)

        self._run = libchord.polychord_c_interface
        self._run.restype=None
        self._run.argtypes = polychord_args
        self.converged=False

        self.ndim = len(self.pipeline.varied_params)
        self.nderived = len(self.pipeline.extra_saves)

        #Required options
        self.live_points    = self.read_ini("live_points", int)

        #Output and feedback options
        self.feedback               = self.read_ini("feedback", int, 1)
        self.resume                 = self.read_ini("resume", bool, False)
        self.polychord_outfile_root = self.read_ini("polychord_outfile_root", str, "")
        self.compression_factor     = self.read_ini("compression_factor", double, np.exp(-1))

        #General run options
        self.max_iterations = self.read_ini("max_iterations", int, -1)
        self.num_repeats = self.read_ini("num_repeats", int, self.ndims*5)
        self.nprior = self.read_ini("nprior", int, self.nlive*10)
        self.random_seed = self.read_ini("random_seed", int, -1)
        self.tolerance   = self.read_ini("tolerance", float, 0.1)
        self.log_zero    = self.read_ini("log_zero", float, -1e6)

        if self.output:
            def dumper(ndead, nlive, npars, live, dead, logweights, log_z, log_z_err):
                print("Saving %d samples" % ndead)
                self.output_params(ndead, nlive, npars, live, dead, logweights, log_z, log_z_err)
            self.wrapped_output_logger = dumper_type(dumper)
        else:
            def dumper(ndead, nlive, npars, live, dead, logweights, log_z, log_z_err):
                pass
            self.wrapped_output_logger = dumper_type(dumper)

        def prior(cube, theta, nDims):
            theta = self.pipeline.denormalize_vector_from_prior(cube) 
        self.wrapped_prior = prior_type(prior)

        def likelihood(theta, nDims, phi, nDerived):
            try:
                like, phi = self.pipeline.likelihood(theta)
            except KeyboardInterrupt:
                raise sys.exit(1)

            return like
        self.wrapped_likelihood = loglike_type(likelihood)

    def worker(self):
        self.sample()

    def execute(self):
        self.log_z = 0.0
        self.log_z_err = 0.0

        self.sample()

        self.output.final("log_z", self.log_z)
        self.output.final("log_z_error", self.log_z_err)

    def sample(self):

        self._run(
                self.wrapped_likelihood,      #loglike,
                self.wrapped_prior,           #prior,
                self.wrapped_dumper,          #dumper,
                self.live_points,             #nlive
                self.num_repeats,             #nrepeats
                self.nprior,                  #nprior
                True,                         #do_clustering
                self.feedback,                #feedback
                self.tolerance,               #precision_criterion
                self.log_zero,                #logzero
                self.max_iterations,          #max_ndead
                0.,                           #boost_posterior
                self.polychord_outfile_root,  #posteriors
                self.polychord_outfile_root,  #equals
                ct.c_bool,                    #cluster_posteriors
                self.resume,                  #write_resume 
                False,                        #write_paramnames
                self.resume,                  #read_resume
                self.polychord_outfile_root,  #write_stats
                self.polychord_outfile_root,  #write_live
                self.polychord_outfile_root,  #write_dead
                self.polychord_outfile_root,  #write_prior
                self.compression_factor,      #compression_factor
                self.ndim,                    #nDims
                self.nderived,                #nDerived 
                "",                           #base_dir
                self.polychord_outfile_root,  #file_root
                1,                            #nGrade
                [1.],                         #grade_frac
                [self.ndim],                  #grade_dims
                0,                            #n_nlives
                [],                           #loglikes
                [],                           #nlives
                self.random_seed,             #seed
                )

        self.converged = True

    def output_params(selfndead, nlive, npars, live, dead, logweights, log_z, log_z_err):
        self.log_z = log_z
        self.log_z_err = log_z_err
        data = np.array([dead[i] for i in range(npars*ndead)]).reshape((npars, ndead))
        logw = np.array(logweights)
        for row, w in zip(data.T,logw):
            params = row[:self.ndim]
            extra_vals = row[self.ndim:self.ndim+self.nderived]
            birth_like = row[self.ndim+self.nderived]
            like = row[self.ndim+self.nderived+1]
            importance = np.exp(w)
            self.output.parameters(params, extra_vals, like, importance)
        self.output.final("nsample", ndead)
        self.output.flush()

    def is_converged(self):
        return self.converged
