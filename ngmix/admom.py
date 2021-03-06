from __future__ import print_function
import numpy
from numpy import array, diag

from .gmix import GMix, GMixModel
from .shape import e1e2_to_g1g2
from .observation import Observation, ObsList, MultiBandObsList
from . import _gmix
from .gexceptions import GMixRangeError

def run_admom(obs, guess, **kw):
    am=Admom(obs, **kw)

    am.go(guess)

    return am

class Admom(object):
    """
    Measure adaptive moments for the input observation

    parameters
    ----------
    obs: Observation
        ngmix.Observation
    maxiter: integer, optional
        Maximum number of iterations, default 200
    etol: float, optional
        absolute tolerance in e1 or e2 to determine convergence,
        default 1.0e-5
    Ttol: float, optional
        relative tolerance in T <x^2> + <y^2> to determine
        convergence, default 1.0e-3
    shiftmax: float, optional
        Largest allowed shift in the centroid, relative to
        the initial guess.  Default 5.0 (5 pixels if the jacobian
        scale is 1)
    """

    def __init__(self, obs, maxiter=200, shiftmax=5.0,
                 etol=1.0e-5, Ttol=0.001,
                 rng=None,
                 deconv=False,
                 **unused_keys):

        self._set_obs(obs, deconv)
        self._set_conf(maxiter, shiftmax, etol, Ttol)

        self.rng=rng

    def get_result(self):
        """
        get the result
        """

        if not hasattr(self,'result'):
            raise RuntimeError("run go() first")

        return self.result

    def get_gmix(self):
        """
        get a gmix representing the best fit, normalized
        """

        pars=self.result['pars'].copy()
        pars[5]=1.0

        e1 = pars[2]/pars[4]
        e2 = pars[3]/pars[4]

        g1,g2 = e1e2_to_g1g2(e1, e2)
        pars[2] = g1
        pars[3] = g2

        return GMixModel(pars, "gauss")

    def go(self, guess):
        """
        run the adpative moments

        parameters
        ----------
        guess_gmix: ngmix.GMix or a float
            A guess for the fitter.  Can be a full gaussian
            mixture or a single value for T, in which case
            the rest of the parameters are random numbers
            about the jacobian center and zero ellipticity
        """

        if isinstance(guess,GMix):
            guess_gmix=guess
        else:
            Tguess = guess
            guess_gmix = self._generate_guess(Tguess)

        res=self._go(guess_gmix)


    def _go(self, guess_gmix):

        ares=self._get_am_result()

        try:
            if self._deconv:
                _gmix.admom_multi_deconv(
                    self.conf,
                    self._imlist,
                    self._wtlist,
                    self._psflist,
                    self._jlist,
                    guess_gmix._data,
                    ares,
                )
            else:
                if len(self._imlist) > 1:
                    _gmix.admom_multi(
                        self.conf,
                        self._imlist,
                        self._wtlist,
                        self._jlist,
                        guess_gmix._data,
                        ares,
                    )
                else:
                    _gmix.admom(
                        self.conf,
                        self._imlist[0],
                        self._wtlist[0],
                        self._jlist[0],
                        guess_gmix._data,
                        ares,
                    )

        except GMixRangeError as err:
            print("caught admom exception: '%s'" % str(err))
            pass

        self.result = copy_result(ares)

    def _set_obs(self, obs, deconv):

        assert deconv==False,"deconv doesn't work yet"
        self._deconv=deconv

        imlist=[]
        wtlist=[]
        jlist=[]

        if deconv:
            self._psflist=[]

        if isinstance(obs,MultiBandObsList):
            mbobs=obs
            for obs_list in mbobs:
                for obs in obs_list:
                    imlist.append(obs.image)
                    wtlist.append(obs.weight)
                    jlist.append(obs.jacobian._data)
                    if deconv and obs.has_psf_gmix():
                        self._psflist.append(obs.psf.gmix._data)

        elif isinstance(obs, ObsList):
            obslist=obs
            for obs in obslist:
                imlist.append(obs.image)
                wtlist.append(obs.weight)
                jlist.append(obs.jacobian._data)
                if deconv and obs.has_psf_gmix():
                    self._psflist.append(obs.psf.gmix._data)

        elif isinstance(obs, Observation):
            imlist.append(obs.image)
            wtlist.append(obs.weight)
            jlist.append(obs.jacobian._data)
            if deconv and obs.has_psf_gmix():
                self._psflist.append(obs.psf.gmix._data)
        else:
            raise ValueError("obs is type '%s' but should be "
                             "Observation, ObsList, or MultiBandObsList")

        if deconv:
            np=len(self._psflist)
            ni=len(imlist)

            if np != ni:
                raise ValueError("only some of obs had psf set: %d/%d" % (np,ni))

        if len(imlist) > 1000:
            raise ValueError("currently limited to 1000 "
                             "images, got %d" % len(imlist))

        self._imlist=imlist
        self._wtlist=wtlist
        self._jlist=jlist

        for wt in wtlist:
            wbad=numpy.where(wt <= 0.0)
            if wbad[0].size > 0:
                raise ValueError("admom found wt <= 0.0")


    def _set_conf(self, maxiter, shiftmax, etol, Ttol):
        dt=numpy.dtype(_admom_conf_dtype, align=True)
        conf=numpy.zeros(1, dtype=dt)

        conf['maxit']=maxiter
        conf['shiftmax']=shiftmax
        conf['etol']=etol
        conf['Ttol']=Ttol

        self.conf=conf

    def _get_am_result(self):
        dt=numpy.dtype(_admom_result_dtype, align=True)
        return numpy.zeros(1, dtype=dt)

    def _get_rng(self):
        if self.rng is None:
            self.rng = numpy.random.RandomState()

        return self.rng

    def _generate_guess(self, Tguess):
        from .gmix import GMixModel

        rng=self._get_rng()

        scale=self._jlist[0]['sdet'][0]
        pars=numpy.zeros(6)
        pars[0:0+2] = rng.uniform(low=-0.5*scale, high=0.5*scale, size=2)
        pars[2:2+2] = rng.uniform(low=-0.3, high=0.3, size=2)
        pars[4]     = Tguess*(1.0 + rng.uniform(low=-0.1, high=0.1))
        pars[5]     = 1.0

        return GMixModel(pars, "gauss")

def get_ratio_error(a, b, var_a, var_b, cov_ab):
    """
    get a/b and error on a/b
    """
    from math import sqrt

    var = get_ratio_var(a, b, var_a, var_b, cov_ab)

    if var < 0:
        var=0
    error = sqrt(var)
    return error

def get_ratio_var(a, b, var_a, var_b, cov_ab):
    """
    get (a/b)**2 and variance in mean of (a/b)
    """

    if b==0:
        raise ValueError("zero in denominator")

    rsq = (a/b)**2

    var = rsq * (  var_a/a**2 + var_b/b**2 - 2*cov_ab/(a*b) )
    return var

def get_sum_err(var_a, var_b, cov_ab):
    from math import sqrt
    var = get_sum_var(var_a, var_b, cov_ab)
    if var < 0:
        var=0

    error = sqrt(var)
    return error

def get_sum_var(var_a, var_b, cov_ab):
    return var_a + var_b - 2*cov_ab

def copy_result(ares):
    """
    copy the result structure to a dict, and
    calculate a few more things
    """

    if isinstance(ares, numpy.ndarray):
        ares=ares[0]
        names = ares.dtype.names
    else:
        names = list(ares.keys())

    res={}
    for n in names:
        if n == 'sums':
            res[n] = ares[n].copy()
        elif n=='sums_cov':
            res[n] = ares[n].reshape( (6,6)).copy()
        else:
            res[n] = ares[n]


    res['flux_mean']  = -9999.0
    res['s2n']   = -9999.0
    res['e']     = numpy.array([-9999.0, -9999.0])
    res['e_err'] = 9999.0

    if res['flags']==0:

        flux_sum=res['sums'][5]
        res['flux_mean'] = flux_sum/res['wsum']
        res['pars'][5] = res['flux_mean']

        # now want pars and cov for [cen1,cen2,e1,e2,T,flux]
        sums=res['sums']

        pars=res['pars']
        sums_cov=res['sums_cov']

        res['T'] = pars[4]

        if sums[5] > 0.0:
            # the sums include the weight, so need factor of two to correct
            res['T_err'] = 4*get_ratio_error(
                sums[4],
                sums[5],
                sums_cov[4,4],
                sums_cov[5,5],
                sums_cov[4,5],
            )

        if res['T'] > 0.0:
            res['e'][:] = res['pars'][2:2+2]/res['T']

            sums=res['sums']
            res['e1err'] = 2*get_ratio_error(
                sums[2],
                sums[4],
                sums_cov[2,2],
                sums_cov[4,4],
                sums_cov[2,4],
            )
            res['e2err'] = 2*get_ratio_error(
                sums[3],
                sums[4],
                sums_cov[3,3],
                sums_cov[4,4],
                sums_cov[3,4],
            )

            if (not numpy.isfinite(res['e1err']) or
                    not numpy.isfinite(res['e2err'])):
                res['e1err']=9999.0
                res['e2err']=9999.0
                res['e_cov']=diag( [9999.0,9999.0] )
            else:
                res['e_cov'] = diag([res['e1err']**2, res['e2err']**2])

        else:
            res['flags'] = 0x8

        fvar_sum=sums_cov[5,5]

        if fvar_sum > 0.0:

            flux_err = numpy.sqrt(fvar_sum)
            res['s2n'] = flux_sum/flux_err

            # error on each shape component from BJ02 for gaussians
            # assumes round

            res['e_err_r'] = 2.0/res['s2n']
        else:
            res['flags'] = 0x40

    res['flagstr'] = _admom_flagmap[res['flags']]

    return res

_admom_conf_dtype=[
    ('maxit','i4'),
    ('shiftmax','f8'),
    ('etol','f8'),
    ('Ttol','f8'),
]
_admom_result_dtype=[
    ('flags','i4'),

    ('numiter','i4'),

    ('nimage','i4'),
    ('npix','i4'),

    ('wsum','f8'),

    ('sums','f8',6),
    ('sums_cov','f8', 36),

    ('pars','f8',6),
]

_admom_flagmap={
    0:'ok',
    0x1:'edge hit', # not currently used
    0x2:'center shifted too far',
    0x4:'flux < 0',
    0x8:'T < 0',
    0x10:'determinant near zero',
    0x20:'maxit reached',
    0x40:'zero var',
}
