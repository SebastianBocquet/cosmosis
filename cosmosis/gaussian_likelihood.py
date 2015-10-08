import scipy.interpolate
import scipy.integrate
import scipy.linalg
import numpy as np
from cosmosis.datablock import names, SectionOptions
import traceback 


MISSING = "if_you_see_this_there_was_a_mistake_creating_a_gaussian_likelihood"


class GaussianLikelihood(object):
    """
    Gaussian likelihood with a fixed covariance.  
    
    Subclasses must override build_data and build_covariance,
    e.g. to load from file.
    """
    x_section = MISSING
    x_name    = MISSING
    y_section = MISSING
    y_name    = MISSING
    like_name = MISSING

    #Set this to False to load the covariance at 
    #each cosmology instead of once at the start
    constant_covariance = True

    def __init__(self, options):
        self.options=options
        self.data_x, self.data_y = self.build_data()
        if self.constant_covariance:
            self.cov = self.build_covariance()
            self.inv_cov = self.build_inverse_covariance()
        self.kind = self.kwargs.get("kind", "cubic")

        #Allow over-riding where the inputs come from in 
        #the options section
        if options.has_value("x_section"):
            self.x_section = options['x_section']
        if options.has_value("y_section"):
            self.y_section = options['y_section']
        if options.has_value("x_name"):
            self.x_name = options['x_name']
        if options.has_value("y_name"):
            self.y_name = options['y_name']
        if options.has_value("like_name"):
            self.like_name = options['like_name']



    def build_data(self):
        """
        Override the build_data method to read or generate 
        the observed data vector to be used in the likelihood
        """
        raise RuntimeError("Your Gaussian covariance code needs to "
            "over-ride the build_data method so it knows how to "
            "load the observed data")
        #using info in self.options,
        #like filenames etc,
        #build x to which we must interpolate
        #return x, y
    
    def build_covariance(self):
        """
        Override the build_covariance method to read or generate 
        the observed covariance
        """
        raise RuntimeError("Your Gaussian covariance code needs to "
            "over-ride the build_covariance method so it knows how to "
            "load the data covariance (or set constant_covariance=F and "
            "over-ride the extract_covariance method)")

        #using info in self.options,
        #like filenames etc,
        #build covariance

    def build_inverse_covariance(self):
        """
        Override the build_inverse_covariance method to change
        how the inverse is generated from the covariance.

        When the covariance is generated from a suite of simulations,
        for example, the simple inverse is not the best estimate.

        """
        return np.linalg.inv(self.cov)


    def cleanup(self):
        """
        You can override the cleanup method if you do something 
        unusual to get your data, like open a database or something.
        It is run just once, at the end of the pipeline.
        """
        pass
    
    def extract_covariance(self, block):
        """
        Override this and set constant_covariance=False
        to enable a cosmology-dependent covariance.

        Load the covariance from the block here.
        """
        raise RuntimeError("You need to implement the method "
            "'extract_covariance' if you set constant_covariance=True "
            "in a gaussian likelihood")

    def extract_inverse_covariance(self, block):
        """
        Override this and set constant_covariance=False
        to enable a cosmology-dependent inverse
        covariance matrix.

        By default the inverse is just directly calculated from
        the covariance, but you might have a different method.
        """
        return np.linalg.inv(self.cov)


    def do_likelihood(self, block):
        #get data x by interpolation
        x = self.extract_theory_points(block)
        mu = self.data_y

        if not self.constant_covariance:
            self.cov = self.extract_covariance(block)
            self.inv_cov = self.extract_inverse_covariance(block)

        #gaussian likelihood
        d = x-mu
        like = -0.5*np.einsum('i,ij,j', d, self.inv_cov, d)

        #Now save the resulting likelihood
        block[names.likelihoods, self.like_name+"_LIKE"] = like

        #And also the predicted data points - the vector of observables 
        # that in a fisher approch we want the derivatives of.
        #and inverse cov mat which also goes into the fisher matrix.
        block[names.data_vector, self.like_name + "_theory"] = np.atleast_1d(x)
        block[names.data_vector, self.like_name + "_data"] = np.atleast_1d(mu)
        block[names.data_vector, self.like_name + "_covariance"] = np.atleast_2d(self.cov)
        block[names.data_vector, self.like_name + "_inverse_covariance"] = np.atleast_2d(self.inv_cov)

        #Also save a simulation of the data - the mean with added noise
        #these can be used among other places by the ABC sampler.
        sim = self.simulate_data_vector(x)
        block[names.data_vector, self.like_name + "_simulation"] = sim

    def simulate_data_vector(self, x):
        "Simulate a data vector by adding a realization of the covariance to the mean"
        #generate a vector of normal deviates
        r = np.random.randn(x.size)
        return x + np.dot(self.cov, r)


    def extract_theory_points(self, block):
        "Extract relevant theory from block and get theory at data x values"
        theory_x = block[self.x_section, self.x_name]
        theory_y = block[self.y_section, self.y_name]
        return self.generate_theory_points(theory_x, theory_y)

    def generate_theory_points(self, theory_x, theory_y):
        "Generate theory predicted data points by interpolation into the theory"
        f = scipy.interpolate.interp(theory_x, theory_y, kind=self.kind)
        return np.atleast_1d(f(self.data_x))

    @classmethod
    def build_module(cls):

        def setup(options):
            options = SectionOptions(options)
            likelihoodCalculator = cls(options)
            return likelihoodCalculator

        def execute(block, config):
            likelihoodCalculator = config
            try:
                likelihoodCalculator.do_likelihood(block)
                return 0
            except Exception as error:
                print "Error getting likelihood:"
                print traceback.format_exc()
                return 1

        def cleanup(config):
            likelihoodCalculator = config
            likelihoodCalculator.cleanup()


        return setup, execute, cleanup


class SingleValueGaussianLikelihood(GaussianLikelihood):
    """
    A Gaussian likelihood whos input is a single calculated value
    not a vector
    """
    name = MISSING
    section = MISSING
    like_name = MISSING
    mean = None
    sigma = None
    def __init__(self, options):
        self.options=options

        #First try getting the value from the class itself
        mean, sigma = self.build_data()

        if options.has_value("mean"):
            mean = options["mean"]
        if options.has_value("sigma"):
            sigma = options["sigma"]

        if sigma is None or mean is None:
            raise ValueError("Need to specify Gaussian mean/sigma for '{0}' \
                either in class definition, build_data method, or in the ini \
                file".format(self.like_name))
        if options.has_value("like_name"):
            self.like_name = options["like_name"]
        print 'Likelihood "{0}" will be Gaussian {1} +/- {2} '.format(self.like_name, self.mean, self.sigma)
        self.data_y = np.array([mean])
        self.cov = np.array([[sigma**2]])
        self.inv_cov = np.array([[sigma**-2]])

    def build_data(self):
        """Sub-classes can over-ride this if they wish, to generate 
        the data point in a more complex way"""
        return self.mean, self.sigma

    def build_covariance(self):
        """This method is only defined here to satisfy the superclass requirements. 
        There is no point over-riding it"""
        raise RuntimeError("Internal cosmosis error in SingleValueGaussianLikelihood")

    def extract_theory_points(self, block):
        "Extract relevant theory from block and get theory at data x values"
        return np.atleast_1d(block[self.section, self.name])



class WindowedGaussianLikelihood(GaussianLikelihood):
    def __init__(self, options):
        super(WindowedGaussianLikelihood, self).__init__(options)
        self.windows = self.build_windows()

    def build_windows(self):
        raise RuntimeError("When you set up a new windowed Gaussian likelihood you need to write the build_windows method")

    def generate_theory_points(self, theory_x, theory_y):
        "Generate theory predicted data points using window function"
        f = scipy.interpolate.interp(theory_x, theory_y, kind=self.kind)
        values = []
        for window_x, window_y in self.windows:
            xmin = max(window_x.min(), theory_x.min())
            xmax = min(window_x.max(), theory_x.max())
            w = scipy.interpolate.interp(window_x, window_y, kind=self.kind)
            g = lambda x: w(x)*f(x)
            v = scipy.integrate.romberg(g, xmin, xmax)
            values.append(v)
        return np.atleast_1d(values)
        

