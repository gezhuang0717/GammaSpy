"""!
@brief Module roi.
Contains region of interest model def
"""
from __future__ import division
import gammaspy.gammaData.peak as peak
import gammaspy.gammaData.bg as bg
from scipy.odr import Model, Data, ODR
from scipy.signal import savgol_filter
from scipy.optimize import curve_fit
import numpy as np


class Roi(object):
    """!
    @brief Region of interest (ROI)
    which can be further broken into three subregions:
    a left background, peak, and right background.
    @verbatim
                   .._.
                 ._    _.
                _        _
    ....----._.-          --_...-..._....
    |   l_bg   |   peak     |   r_bg   |
    @endverbatim
    """
    def __init__(self, spectrum, centroid=1000.):
        self._centroid = centroid
        self.bg_bounds = [self._centroid - 12.,
                          self._centroid - 1.,
                          self._centroid + 1.,
                          self._centroid + 12.]
        self._peak_models = ["gauss"]
        self._bg_models = ["linear"]
        # composition
        self.peak_model = peak.GaussModel([100., self._centroid, 1.])
        self.bg_model = bg.LinModel()
        self._init_params = np.concatenate((self.bg_model.params, self.peak_model.params))
        # data stor
        self.roi_data_orig = spectrum
        self.roi_data = np.array([])
        self.update_data(spectrum)

    @property
    def lbound(self):
        return self.bg_bounds[0]

    @lbound.setter
    def lbound(self, lbound):
        self.bg_bounds[0] = lbound
        self.update_data(self.roi_data_orig)

    @property
    def ubound(self):
        return self.bg_bounds[-1]

    @ubound.setter
    def ubound(self, ubound):
        self.bg_bounds[-1] = ubound
        self.update_data(self.roi_data_orig)

    def update_data(self, spectrum=None):
        """!
        @brief Updates data contained in ROI when self.bg_bounds changes
        """
        if spectrum is None:
            spectrum = self.roi_data
        selection = (spectrum[:, 0] > self.bg_bounds[0]) & (spectrum[:, 0] < self.bg_bounds[-1])
        self.roi_data = spectrum[selection]

    def find_roi(self, threshold=50., wl=5, tailbuf=4., **kwargs):
        """!
        @brief Try to auto find the ROI by walking down the peak while checking
        the second derivative to exceed some positive threshold.
        Optionally smooths the data first.
        @param threshold  Threshold second deriv value at which to stop roi search
        @param wl  Number of points to include in each smoothing window
        @param tailbuf  Float. Extra roi tail length in (KeV)
        """
        y_2div = savgol_filter(self.roi_data_orig[:, 1], window_length=wl, polyorder=3, deriv=2)
        # y_1div = savgol_filter(self.roi_data_orig[:, 1], window_length=wl, polyorder=3, deriv=1)
        roi_data_2div = np.array([self.roi_data_orig[:, 0], y_2div]).T
        # start at centroid and walk left
        l_mask = (self.roi_data_orig[:, 0] <= self._centroid)
        l_data = roi_data_2div[l_mask]
        # start at centroid and walk right
        r_mask = (self.roi_data_orig[:, 0] >= self._centroid)
        r_data = roi_data_2div[r_mask]
        for i, l_2div in enumerate(l_data[::-1]):
            if l_2div[1] > threshold:
                self.lbound = l_2div[0] - tailbuf
                break
        for i, r_2div in enumerate(r_data):
            if r_2div[1] > threshold:
                self.ubound = r_2div[0] + tailbuf
                break
        # self.update_data()
        print("Done fitting ROI")
        print("Lower Bound: %f, Upper Bound: %f" % (self.lbound, self.ubound))

    @property
    def centroid(self):
        """!
        @brief Peak center
        """
        return self._centroid

    @property
    def peak_models(self):
        """!
        @brief Peak models to consider when fitting.
        """
        return self._peak_models

    @property
    def bg_models(self):
        """!
        @brief Background models to consider when fitting.
        """
        return self._peak_models

    @property
    def init_params(self):
        return self._init_params

    @init_params.setter
    def init_params(self, init_params):
        self._init_params = init_params

    def set_peak_model(self):
        """!
        @brief Set ODR model
        """
        x = self.roi_data[:, 0]
        y = self.roi_data[:, 1]
        data = Data(x, y)
        #
        bgn = len(self.bg_model.params)
        self.tot_model = lambda p, X: self.bg_model.eval(p[:bgn], X) + self.peak_model.eval(p[bgn:], X)
        #self.tot_model = lambda p, X: self.bg_model.eval(p[:bgn], X)
        print("Initial Model Params")
        print(self._init_params)
        self.odr_model = ODR(data, Model(self.tot_model), beta0=self._init_params, ifixb=[1, 1, 1, 0, 1], maxit=800, taufac=0.8)

    def fit(self):
        """!
        @brief Fit model via non-linear least squares.
        Simulataneously fits background and peak
        """
        x = self.roi_data[:, 0]
        y = self.roi_data[:, 1]
        bgn = len(self.bg_model.params)
        self.tot_model = lambda p, X: self.bg_model.eval(p[:bgn], X) + self.peak_model.eval(p[bgn:], X)
        def opti_model(x, *params):
            return self.bg_model.opti_eval(x, *params[:bgn]) + self.peak_model.opti_eval(x, *params[bgn:])
        self.popt, self.pcov = curve_fit(opti_model, x, y, p0=self._init_params)
        self.perr = np.sqrt(np.diag(self.pcov))
        print("================================")
        print("Scipy optimal coeffs: ")
        print(self.popt)
        print("Scipy coeff covar matrix: ")
        print(self.pcov)
        print("================================")
        self.y_hat = self.tot_model(self.popt, self.roi_data[:, 0])
        self.net_area()

    def net_area(self):
        """!
        @brief Peak - Background
        """
        bgn = len(self.bg_model.params)
        area_peak = self.peak_model.area(self.popt[bgn:])
        area_bg = self.bg_model.integral(self.lbound, self.ubound, self.popt[:bgn])
        net = area_peak # - area_bg
        # uncertainty calc
        area_peak_jac = self.peak_model.area_jac(self.popt[bgn:])
        area_bg_jac = self.bg_model.int_jac(self.lbound, self.ubound, self.popt[:bgn])
        if len(area_peak_jac.shape) == 2:
            all_jac = np.concatenate((area_bg_jac[0], area_peak_jac[0]))
        else:
            all_jac = np.concatenate((area_bg_jac, area_peak_jac))
        # std prop of uncetainty J * C * J.T
        uncert = np.dot(all_jac, self.pcov)
        uncert = np.dot(uncert, all_jac.T)
        print("Area= %f +/- %f (1sigma) " % (net, np.sqrt(np.sum(uncert))))

    def total_area(self):
        """!
        @brief Integral of peak + bg
        """
        pass

    def odr_fit(self):
        """!
        @brief Fit model via orthogonal dist regression.
        Simulataneously fits background and peak
        """
        self.set_peak_model()
        # 1SD uncert in fitted params = self.fit_output.sd_beta
        # fitted func values at input x = self.fit_output.y
        self.fit_output = self.odr_model.run()
        print("================================")
        print(self.fit_output.pprint())
        print("================================")
        self.y_hat = self.tot_model(self.fit_output.beta, self.roi_data[:, 0])

    def fit_mcmc(self):
        """!
        @brief Fit peak by marcov chain monte carlo
        """
        pass
